import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple


FAILURE_LABELS = {
    "auth": "Autenticacao",
    "timeout": "Timeout",
    "port_refused": "Porta recusada",
    "vpn": "VPN",
    "no_ping": "Sem ping",
    "connection": "Conectividade",
    "script": "Script",
    "unknown": "Outros",
}

TRANSIENT_FAILURE_CATEGORIES = {
    "timeout",
    "connection",
    "vpn",
}

_ROUTEROS_TIMESTAMP_RE = re.compile(r"^\s*#\s+.+\s+by\s+RouterOS\s+.+$", re.IGNORECASE)
_VOLATILE_LINE_PATTERNS = [
    re.compile(r"^\s*#\s+(jan|fev|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[/\s-]", re.IGNORECASE),
    re.compile(r"^\s*current configuration\s*:.*$", re.IGNORECASE),
    re.compile(r"^\s*building configuration.*$", re.IGNORECASE),
    re.compile(r"^\s*last configuration change.*$", re.IGNORECASE),
    re.compile(r"^\s*ntp clock-period.*$", re.IGNORECASE),
    re.compile(r"^\s*!\s*time:\s*.*$", re.IGNORECASE),
    re.compile(r"^\s*!\s*generated.*$", re.IGNORECASE),
]


def classify_failure(message: str) -> str:
    text = (message or "").strip().lower()
    if not text:
        return "unknown"

    if any(k in text for k in ["sem resposta ao ping", "no ping", "icmp", "100% packet loss"]):
        return "no_ping"
    if any(k in text for k in ["auth", "autentic", "credencia", "senha", "password", "access denied", "permission denied", "login failed", "unauthorized", "invalid credentials"]):
        return "auth"
    if any(k in text for k in ["timed out", "timeout", "timeoutexception", "softtimelimitexceeded"]):
        return "timeout"
    if any(k in text for k in ["connection refused", "refused", "errno 111", "port closed"]):
        return "port_refused"
    if any(k in text for k in ["vpn", "nmcli", "l2tp", "ipsec", "ppp"]):
        return "vpn"
    if any(k in text for k in ["script", "traceback", "syntaxerror", "attributeerror", "typeerror", "keyerror", "modulenotfound", "importerror"]):
        return "script"
    if any(k in text for k in ["eof", "connection closed", "socket", "network", "host unreachable", "no route to host", "tcp connection", "ssh", "telnet"]):
        return "connection"
    return "unknown"


def failure_label(category: str) -> str:
    return FAILURE_LABELS.get((category or "").strip().lower(), FAILURE_LABELS["unknown"])


def is_transient_failure(category: str) -> bool:
    return (category or "").strip().lower() in TRANSIENT_FAILURE_CATEGORIES


def parse_iso_utc(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def is_connection_ready_recent(extra_parameters: Dict[str, Any], max_age_minutes: int = 30, now_utc: Optional[datetime] = None) -> Tuple[bool, str]:
    extra = extra_parameters if isinstance(extra_parameters, dict) else {}
    group = str(extra.get("connection_test_group") or "").strip().lower()
    if group != "ready":
        return False, "sem ping+login OK no ultimo teste"

    last_at = parse_iso_utc(extra.get("connection_test_last_at"))
    if not last_at:
        return False, "sem timestamp do ultimo teste ping/login"

    now = now_utc or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=max(1, int(max_age_minutes or 30)))
    if last_at < cutoff:
        return False, f"teste ping/login desatualizado (> {int(max_age_minutes or 30)} min)"

    return True, "ready"


def normalize_config_lines(content: str) -> Tuple[list[str], int]:
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    dropped = 0
    normalized = []
    for raw in lines:
        line = raw.rstrip()
        if _ROUTEROS_TIMESTAMP_RE.match(line):
            dropped += 1
            continue
        if any(p.match(line) for p in _VOLATILE_LINE_PATTERNS):
            dropped += 1
            continue
        normalized.append(line)

    # Remove excesso de linhas em branco consecutivas.
    compact = []
    blank_streak = 0
    for line in normalized:
        if not line.strip():
            blank_streak += 1
            if blank_streak > 1:
                dropped += 1
                continue
        else:
            blank_streak = 0
        compact.append(line)
    return compact, dropped


def validate_backup_integrity(file_path: Optional[str], device_type_name: str = "", script_name: str = "") -> Dict[str, Any]:
    result = {
        "ok": False,
        "reason": "",
        "size_bytes": 0,
        "line_count": 0,
        "markers_found": [],
    }
    if not file_path:
        result["reason"] = "arquivo ausente"
        return result

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as exc:
        result["reason"] = f"nao foi possivel ler arquivo: {exc}"
        return result

    size_bytes = len(content.encode("utf-8", errors="ignore"))
    lines = content.splitlines()
    line_count = len(lines)
    result["size_bytes"] = size_bytes
    result["line_count"] = line_count

    if size_bytes < 128:
        result["reason"] = "arquivo muito pequeno (<128 bytes)"
        return result
    if line_count < 5:
        result["reason"] = "arquivo com poucas linhas (<5)"
        return result

    token = f"{(device_type_name or '').lower()} {(script_name or '').lower()} {(content or '').lower()[:4000]}"
    markers = []

    marker_sets = [
        ("routeros", ["/interface", "/ip", "routeros", "# model ="]),
        ("huawei", ["display current-configuration", "sysname", "interface", "vlan"]),
        ("zte", ["show running-config", "interface", "vlan"]),
        ("fiberhome", ["show running-config", "terminal length 0", "interface"]),
        ("switch", ["interface", "vlan", "hostname"]),
        ("cisco", ["version", "hostname", "interface"]),
    ]

    expected = []
    if any(k in token for k in ["mikrotik", "routeros"]):
        expected = marker_sets[0][1]
    elif "huawei" in token:
        expected = marker_sets[1][1]
    elif "zte" in token:
        expected = marker_sets[2][1]
    elif "fiberhome" in token:
        expected = marker_sets[3][1]
    elif "switch" in token:
        expected = marker_sets[4][1]
    elif "cisco" in token:
        expected = marker_sets[5][1]

    if expected:
        content_l = content.lower()
        for marker in expected:
            if marker.lower() in content_l:
                markers.append(marker)

    result["markers_found"] = markers
    if expected and len(markers) == 0:
        result["reason"] = "conteudo sem marcadores esperados para o tipo de equipamento"
        return result

    result["ok"] = True
    result["reason"] = "integridade validada"
    return result
