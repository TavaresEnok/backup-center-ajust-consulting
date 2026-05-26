"""
Tasks Celery para execução de backups.

Essas tasks executam em background para não bloquear o servidor web.
"""

from app.celery_app import celery_app
from app.core.database import SessionLocal
from app.core.config import settings
from app.services.connection_mode import uses_jump_host, uses_vpn_tunnel
from app.services.backup_observability import inc_counter, observe_histogram
import logging
import os
import json
from datetime import datetime, timedelta
from collections import Counter, defaultdict
import time
from contextlib import contextmanager
from celery.exceptions import Retry
from app.services.schedule_utils import compute_next_daily_run_at, sanitize_daily_time, utc_now_naive
from app.services.mass_backup_scope import resolve_mass_backup_excluded_type_ids
from app.services.activity_service import ActivityService

logger = logging.getLogger(__name__)
LARGE_BULK_FAIL_FAST_THRESHOLD = 8
CB_FAILURE_CATEGORIES = {
    "timeout",
    "connection",
    "jump_session_closed",
    "port_refused",
    "no_ping",
    "jump_host_slot_timeout",
}


def _safe_error_text(err, fallback: str = "Erro sem detalhe.") -> str:
    """Normaliza mensagens de erro para evitar logs vazios ('')."""
    if err is None:
        return fallback
    try:
        txt = str(err).strip()
    except Exception:
        txt = ""
    if txt:
        return txt
    name = getattr(getattr(err, "__class__", None), "__name__", "") or ""
    return name or fallback


def _env_int(name: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    raw = str(os.getenv(name, default)).strip()
    try:
        value = int(raw)
    except Exception:
        value = int(default)
    if value < minimum:
        value = minimum
    if maximum is not None and value > maximum:
        value = maximum
    return value


def _normalize_retry_token(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _env_csv_normalized_set(name: str, default: str = "") -> set[str]:
    raw = str(os.getenv(name, default) or "")
    parsed: set[str] = set()
    for chunk in raw.split(","):
        token = _normalize_retry_token(chunk)
        if token:
            parsed.add(token)
    return parsed


JUMP_HOST_LOCK_TIMEOUT_SECONDS = _env_int(
    "JUMP_HOST_SLOT_TTL_SECONDS",
    60 * 20,
    minimum=60,
)
JUMP_HOST_LOCK_WAIT_SECONDS = _env_int(
    "JUMP_HOST_SLOT_WAIT_SECONDS",
    60 * 10,
    minimum=15,
    maximum=60 * 30,
)
JUMP_HOST_LOCK_WAIT_SECONDS_LARGE_BULK = _env_int(
    "JUMP_HOST_SLOT_WAIT_SECONDS_LARGE_BULK",
    min(180, JUMP_HOST_LOCK_WAIT_SECONDS),
    minimum=30,
    maximum=600,
)
JUMP_HOST_MAX_SLOTS = _env_int(
    "JUMP_HOST_MAX_SLOTS",
    3,
    minimum=1,
    maximum=16,
)
JUMP_PHASE_GROUP_STAGGER_SECONDS = _env_int(
    "JUMP_PHASE_GROUP_STAGGER_SECONDS",
    2,
    minimum=0,
    maximum=30,
)
BACKUP_TASK_SOFT_TIME_LIMIT_SECONDS = _env_int(
    "BACKUP_TASK_SOFT_TIME_LIMIT_SECONDS",
    480,
    minimum=120,
    maximum=3600,
)
BACKUP_TASK_TIME_LIMIT_SECONDS = _env_int(
    "BACKUP_TASK_TIME_LIMIT_SECONDS",
    max(BACKUP_TASK_SOFT_TIME_LIMIT_SECONDS + 60, 540),
    minimum=180,
    maximum=3900,
)

LARGE_BULK_TRANSIENT_RETRY_THRESHOLD = _env_int(
    "BULK_TRANSIENT_RETRY_THRESHOLD",
    300,
    minimum=50,
)
LARGE_BULK_TRANSIENT_MAX_RETRIES = _env_int(
    "BULK_TRANSIENT_MAX_RETRIES",
    1,
    minimum=0,
    maximum=2,
)
JUMP_HOST_ADAPTIVE_ENABLED = str(os.getenv("JUMP_HOST_ADAPTIVE_ENABLED", "1")).strip() in {"1", "true", "on", "yes"}
JUMP_HOST_ADAPTIVE_FAIL_STREAK = _env_int("JUMP_HOST_ADAPTIVE_FAIL_STREAK", 4, minimum=2, maximum=20)
JUMP_HOST_ADAPTIVE_SUCCESS_STREAK = _env_int("JUMP_HOST_ADAPTIVE_SUCCESS_STREAK", 20, minimum=5, maximum=200)
JUMP_HOST_ADAPTIVE_COOLDOWN_SECONDS = _env_int("JUMP_HOST_ADAPTIVE_COOLDOWN_SECONDS", 300, minimum=30, maximum=3600)
JUMP_HOST_ADAPTIVE_STREAK_TTL_SECONDS = _env_int("JUMP_HOST_ADAPTIVE_STREAK_TTL_SECONDS", 900, minimum=60, maximum=7200)
JUMP_HOST_ADAPTIVE_MAX_BOOST = _env_int("JUMP_HOST_ADAPTIVE_MAX_BOOST", 2, minimum=0, maximum=8)
JUMP_HOST_ADAPTIVE_SEVERE_FAIL_STREAK = _env_int(
    "JUMP_HOST_ADAPTIVE_SEVERE_FAIL_STREAK",
    2,
    minimum=1,
    maximum=10,
)
JUMP_HOST_ADAPTIVE_SEVERE_COOLDOWN_SECONDS = _env_int(
    "JUMP_HOST_ADAPTIVE_SEVERE_COOLDOWN_SECONDS",
    900,
    minimum=30,
    maximum=7200,
)
JUMP_HOST_ADAPTIVE_SEVERE_DROP_TO_ONE = str(
    os.getenv("JUMP_HOST_ADAPTIVE_SEVERE_DROP_TO_ONE", "1")
).strip().lower() in {"1", "true", "on", "yes"}
BULK_ACTIVE_STALE_SECONDS = _env_int(
    "BULK_ACTIVE_STALE_SECONDS",
    60 * 60 * 6,
    minimum=60 * 5,
    maximum=60 * 60 * 48,
)
CIRCUIT_BREAKER_WINDOW_SECONDS = _env_int("CB_WINDOW_SECONDS", 300, minimum=60, maximum=900)
CIRCUIT_BREAKER_THRESHOLD_PERCENT = _env_int("CB_THRESHOLD_PERCENT", 70, minimum=30, maximum=95)
CIRCUIT_BREAKER_MIN_SAMPLES = _env_int("CB_MIN_SAMPLES", 10, minimum=3, maximum=50)
CIRCUIT_BREAKER_OPEN_SECONDS = _env_int("CB_OPEN_SECONDS", 60, minimum=10, maximum=300)
CB_OPEN_MAX_RETRIES = _env_int("CB_OPEN_MAX_RETRIES", 1, minimum=0, maximum=10)
CB_OPEN_RETRY_BUFFER_SECONDS = _env_int("CB_OPEN_RETRY_BUFFER_SECONDS", 2, minimum=0, maximum=30)
VPN_GLOBAL_LOCK_TIMEOUT_SECONDS = _env_int(
    "VPN_GLOBAL_LOCK_TIMEOUT_SECONDS",
    3600,
    minimum=60,
    maximum=60 * 60 * 6,
)
TRANSIENT_RETRY_DENYLIST_DEVICE_NAMES = _env_csv_normalized_set(
    "BACKUP_TRANSIENT_RETRY_DENYLIST_NAMES",
    "FLASHNET - SW CORE-CABO_S.A.",
)
TRANSIENT_RETRY_DENYLIST_DEVICE_IDS = _env_csv_normalized_set(
    "BACKUP_TRANSIENT_RETRY_DENYLIST_IDS",
    "",
)

_bulk_device_count_cache: dict[str, int] = {}
_jump_host_slot_overrides_cache: dict[str, int] | None = None


class JumpHostSlotTimeoutError(RuntimeError):
    """Timeout aguardando slot de concorrência em Jump Host compartilhado."""


class JumpHostSlotCancelledError(RuntimeError):
    """Execucao interrompida enquanto aguardava slot do Jump Host."""


def _is_global_backup_stop_enabled() -> bool:
    try:
        from app.services.realtime_backup_logs import get_redis_client
        r = get_redis_client()
        if not r:
            return False
        flag = r.get("backup_center:force_stop_backups")
        return str(flag or "").strip() == "1"
    except Exception:
        logger.exception("Falha ao verificar bloqueio global de backups")
        return False


def _is_bulk_cancelled(bulk_task_id: str | None) -> bool:
    if not bulk_task_id:
        return False
    try:
        from app.services.realtime_backup_logs import get_task_meta
        meta = get_task_meta(str(bulk_task_id))
        return bool(meta.get("cancel_requested"))
    except Exception:
        logger.exception("Falha ao verificar cancelamento do lote %s", bulk_task_id)
        return False


def _should_stop_now(bulk_task_id: str | None = None) -> bool:
    return _is_global_backup_stop_enabled() or _is_bulk_cancelled(bulk_task_id)


def reset_circuit_breakers_for_new_batch() -> dict:
    """
    Limpa o estado residual do circuit breaker entre lotes consecutivos.

    A chave 'cb:open:{jump_label}' bloqueia jump hosts com TTL de 60s. Quando um novo
    lote inicia logo apos um lote com muitas falhas, esses bloqueios ainda estao ativos
    no Redis, rejeitando centenas de tasks sem nem tentar. Esta funcao:

    - Remove chaves 'cb:open:*' (os bloqueios ativos).
    - Remove 'cb:outcomes:*' para evitar contaminação entre lotes.
    - Limpa 'jump_host_adaptive*' (reducoes de slot adaptativo anteriores).

    Retorna dict com contagem de chaves removidas para logging.
    """
    from app.services.realtime_backup_logs import get_redis_client

    client = get_redis_client()
    result = {"open_cleared": 0, "adaptive_cleared": 0, "error": None}
    if not client:
        result["error"] = "Redis indisponivel"
        return result

    try:
        open_keys = list(client.scan_iter("backup_center:cb:open:*"))
        if open_keys:
            client.delete(*open_keys)
            result["open_cleared"] = len(open_keys)

        # Limpar o historico de outcomes evita que falhas do lote anterior
        # contaminem o threshold do proximo lote e causem trips prematuros.
        outcomes_keys = list(client.scan_iter("backup_center:cb:outcomes:*"))
        if outcomes_keys:
            client.delete(*outcomes_keys)
            result["outcomes_cleared"] = len(outcomes_keys)

        adaptive_keys = list(client.scan_iter("backup_center:jump_host_adaptive:*"))
        if adaptive_keys:
            client.delete(*adaptive_keys)
            result["adaptive_cleared"] = len(adaptive_keys)

        logger.info(
            "Circuit breakers resetados para novo lote: %d open keys, %d outcomes, %d adaptive keys removidos.",
            result["open_cleared"],
            result.get("outcomes_cleared", 0),
            result["adaptive_cleared"],
        )
    except Exception as exc:
        logger.exception("Falha ao resetar circuit breakers para novo lote.")
        result["error"] = str(exc)

    return result


def _has_active_bulk_operation() -> bool:
    """Evita concorrencia entre scheduler periodico e backups em massa em andamento."""
    from app.services.realtime_backup_logs import get_redis_client

    client = get_redis_client()
    if not client:
        return False

    now_utc = datetime.utcnow()
    try:
        for key in client.scan_iter("backup_center:tenant_active_bulk:*"):
            task_id = client.get(key)
            if not task_id:
                client.delete(key)
                continue
            raw = client.get(f"backup_center:task_meta:{task_id}")
            if not raw:
                client.delete(key)
                continue
            try:
                meta = json.loads(raw)
            except Exception:
                client.delete(key)
                continue
            if not isinstance(meta, dict):
                client.delete(key)
                continue
            if not bool(meta.get("is_bulk")) or bool(meta.get("completed")):
                client.delete(key)
                continue
            operation_kind = str(meta.get("operation_kind") or "backup_bulk").strip().lower()
            if operation_kind not in {"backup_bulk", "backup_reprocess"}:
                continue

            # Usa o timestamp mais recente de atividade conhecida do lote para evitar
            # que lotes longos sejam tratados como "fantasma" e liberem concorrencia indevida.
            activity_candidates = [
                str(meta.get("last_child_activity_at") or "").strip(),
                str(meta.get("updated_at") or "").strip(),
                str(meta.get("created_at") or "").strip(),
            ]
            latest_activity = None
            for value in activity_candidates:
                if not value:
                    continue
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
                except Exception:
                    continue
                if latest_activity is None or parsed > latest_activity:
                    latest_activity = parsed
            if latest_activity is not None:
                if (now_utc - latest_activity).total_seconds() > BULK_ACTIVE_STALE_SECONDS:
                    client.delete(key)
                    continue
            return True
    except Exception:
        logger.exception("Falha ao verificar lotes bulk ativos antes do scheduler.")
        return False
    return False


def _touch_bulk_activity(bulk_task_id: str | None) -> None:
    if not bulk_task_id:
        return
    try:
        from app.services.realtime_backup_logs import update_task_meta

        update_task_meta(
            str(bulk_task_id),
            last_child_activity_at=datetime.utcnow().isoformat() + "Z",
        )
    except Exception:
        logger.exception("Falha ao atualizar heartbeat do lote %s", bulk_task_id)


def _sleep_with_stop_poll(delay_seconds: int | float, bulk_task_id: str | None = None) -> bool:
    """Espera em pequenos intervalos para permitir cancelamento mais responsivo."""
    remaining = max(0.0, float(delay_seconds or 0))
    while remaining > 0:
        if _should_stop_now(bulk_task_id):
            return True
        step = min(1.0, remaining)
        time.sleep(step)
        remaining -= step
    return _should_stop_now(bulk_task_id)


def _get_bulk_device_count(bulk_task_id: str | None) -> int:
    if not bulk_task_id:
        return 0
    bulk_id = str(bulk_task_id).strip()
    if not bulk_id:
        return 0
    if bulk_id in _bulk_device_count_cache:
        return _bulk_device_count_cache[bulk_id]
    try:
        from app.services.realtime_backup_logs import get_task_meta

        meta = get_task_meta(bulk_id) or {}
        total = int(meta.get("total_devices") or meta.get("total_tasks") or 0)
        total = max(0, total)
        _bulk_device_count_cache[bulk_id] = total
        return total
    except Exception:
        logger.exception("Falha ao obter tamanho do lote %s para politica de retry", bulk_id)
        return 0


def _max_transient_retries_for_bulk(bulk_task_id: str | None) -> int:
    # Politica unificada: cada dispositivo pode retentar no maximo 1x.
    return LARGE_BULK_TRANSIENT_MAX_RETRIES


def _is_large_bulk_operation(bulk_task_id: str | None) -> bool:
    return _get_bulk_device_count(bulk_task_id) >= LARGE_BULK_TRANSIENT_RETRY_THRESHOLD


def _effective_jump_host_wait_seconds(bulk_task_id: str | None) -> int:
    if _is_large_bulk_operation(bulk_task_id):
        return max(30, min(JUMP_HOST_LOCK_WAIT_SECONDS, JUMP_HOST_LOCK_WAIT_SECONDS_LARGE_BULK))
    return JUMP_HOST_LOCK_WAIT_SECONDS


def _emit_structured_event(event_name: str, **payload) -> None:
    data = {"event": str(event_name or "unknown"), "ts": datetime.utcnow().isoformat() + "Z"}
    for key, value in (payload or {}).items():
        if value is None:
            continue
        data[str(key)] = value
    try:
        logger.info("backup_event %s", json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    except Exception:
        logger.exception("Falha ao emitir evento estruturado: %s", event_name)


def _parse_jump_host_slot_overrides() -> dict[str, int]:
    global _jump_host_slot_overrides_cache
    if _jump_host_slot_overrides_cache is not None:
        return _jump_host_slot_overrides_cache
    raw = str(os.getenv("JUMP_HOST_SLOTS_OVERRIDES", "") or "").strip()
    parsed: dict[str, int] = {}
    if not raw:
        _jump_host_slot_overrides_cache = parsed
        return parsed
    for chunk in raw.split(","):
        item = chunk.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        try:
            slots = int(value.strip())
        except Exception:
            continue
        parsed[key] = max(1, min(16, slots))
    _jump_host_slot_overrides_cache = parsed
    return parsed


def _resolve_effective_jump_host_slots(client, jump_label: str, base_slots: int) -> int:
    effective = max(1, int(base_slots or 1))
    overrides = _parse_jump_host_slot_overrides()
    if jump_label in overrides:
        effective = overrides[jump_label]
    elif "default" in overrides:
        effective = overrides["default"]

    if not JUMP_HOST_ADAPTIVE_ENABLED or not client:
        return max(1, min(16, effective))
    try:
        raw_dynamic = client.hget("backup_center:jump_host_adaptive_slots", jump_label)
        if raw_dynamic is not None:
            dynamic_slots = int(raw_dynamic)
            effective = max(1, min(16, dynamic_slots))
    except Exception:
        logger.exception("Falha ao ler slots adaptativos para jump host %s", jump_label)
    return max(1, min(16, effective))


def _message_indicates_jump_host_saturation(message: str | None) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    return any(
        marker in text
        for marker in (
            "error reading ssh protocol banner",
            "timeout opening channel",
            "channelexception",
            "no existing session",
            "sessao com jump host encerrada",
            "sessão com jump host encerrada",
            "banner exchange",
            "kex_exchange_identification",
            "jump host",
            "slot do jump host",
        )
    )


def _message_indicates_severe_jump_host_saturation(message: str | None) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    return any(
        marker in text
        for marker in (
            "sessao com jump host encerrada antes do shell ficar disponivel",
            "sessão com jump host encerrada antes do shell ficar disponivel",
            "nao foi possivel abrir shell interativo no jump host",
            "não foi possível abrir shell interativo no jump host",
            "error reading ssh protocol banner",
            "timeout opening channel",
            "kex_exchange_identification",
            "connection reset by peer",
        )
    )


def _is_nmcli_backend_unavailable_message(message: str | None) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    markers = (
        "networkmanager indisponivel neste worker",
        "networkmanager indisponível neste worker",
        "backend do networkmanager indisponivel",
        "backend do networkmanager indisponível",
        "nmcli general status",
        "could not create nmclient object",
        "could not connect: no such file or directory",
        "nmcli nao encontrado",
        "nmcli não encontrado",
    )
    return any(marker in text for marker in markers)


def _is_transient_retry_denied_for_device(device_name: str | None = None, device_id: str | None = None) -> bool:
    if not TRANSIENT_RETRY_DENYLIST_DEVICE_NAMES and not TRANSIENT_RETRY_DENYLIST_DEVICE_IDS:
        return False
    normalized_name = _normalize_retry_token(device_name)
    normalized_id = _normalize_retry_token(device_id)
    if normalized_id and normalized_id in TRANSIENT_RETRY_DENYLIST_DEVICE_IDS:
        return True
    if normalized_name and normalized_name in TRANSIENT_RETRY_DENYLIST_DEVICE_NAMES:
        return True
    return False


def _should_retry_transient_failure(
    category: str | None,
    message: str | None,
    *,
    device_name: str | None = None,
    device_id: str | None = None,
) -> bool:
    normalized_category = str(category or "").strip().lower()
    if not normalized_category:
        return False

    from app.services.backup_diagnostics import is_transient_failure

    if not is_transient_failure(normalized_category):
        return False

    text = str(message or "").strip().lower()

    # Falha estrutural do worker VPN (nmcli/networkmanager ausente): nao adianta retentar.
    if _is_nmcli_backend_unavailable_message(text):
        return False

    # Quarentena de dispositivos cronicos para nao travar lotes com retentativas longas.
    if _is_transient_retry_denied_for_device(device_name=device_name, device_id=device_id):
        return False

    if normalized_category == "connection":
        hard_markers = (
            "connection refused",
            "no route to host",
            "network is unreachable",
            "unable to connect to remote host",
            "unable to connect to port",
            "timeout opening channel",
            "administratively prohibited",
            "jump host nao conseguiu abrir canal tcp",
            "jump host não conseguiu abrir canal tcp",
            "authentication failed",
            "invalid username",
            "invalid password",
            "unauthorized",
            "access denied",
            "permission denied",
            "wrong tcp port",
            "incorrect hostname",
        )
        if any(marker in text for marker in hard_markers):
            return False
    return True


def _adapt_jump_host_slots(lock_info: dict | None, success: bool, message: str | None) -> None:
    if not lock_info or not JUMP_HOST_ADAPTIVE_ENABLED:
        return
    jump_label = str(lock_info.get("label") or "").strip()
    if not jump_label:
        return
    base_slots = max(1, int(lock_info.get("base_slots") or lock_info.get("max_slots") or 1))

    from app.services.realtime_backup_logs import get_redis_client

    client = get_redis_client()
    if not client:
        return

    slots_hash = "backup_center:jump_host_adaptive_slots"
    fail_streak_key = f"backup_center:jump_host_adaptive:fail:{jump_label}"
    success_streak_key = f"backup_center:jump_host_adaptive:success:{jump_label}"
    cooldown_key = f"backup_center:jump_host_adaptive:cooldown:{jump_label}"
    allowed_max_slots = max(1, min(16, base_slots + JUMP_HOST_ADAPTIVE_MAX_BOOST))
    current_slots = _resolve_effective_jump_host_slots(client, jump_label, base_slots)

    # Do not churn slot limits while in cooldown.
    try:
        if client.get(cooldown_key):
            return
    except Exception:
        return

    if success:
        try:
            success_streak = int(client.incr(success_streak_key))
            client.expire(success_streak_key, JUMP_HOST_ADAPTIVE_STREAK_TTL_SECONDS)
            client.delete(fail_streak_key)
            if success_streak >= JUMP_HOST_ADAPTIVE_SUCCESS_STREAK and current_slots < allowed_max_slots:
                next_slots = min(allowed_max_slots, current_slots + 1)
                client.hset(slots_hash, jump_label, next_slots)
                client.setex(cooldown_key, JUMP_HOST_ADAPTIVE_COOLDOWN_SECONDS, "1")
                client.delete(success_streak_key)
                inc_counter(
                    "jump_host_adaptive_slot_changes_total",
                    labels={"jump_host": jump_label, "direction": "up"},
                )
                _emit_structured_event(
                    "jump_host_slots_changed",
                    jump_host=jump_label,
                    direction="up",
                    previous_slots=current_slots,
                    new_slots=next_slots,
                    reason="success_streak",
                )
        except Exception:
            logger.exception("Falha ao ajustar slots adaptativos (success) para %s", jump_label)
        return

    if not _message_indicates_jump_host_saturation(message):
        return
    try:
        fail_streak = int(client.incr(fail_streak_key))
        client.expire(fail_streak_key, JUMP_HOST_ADAPTIVE_STREAK_TTL_SECONDS)
        client.delete(success_streak_key)
        severe_saturation = _message_indicates_severe_jump_host_saturation(message)
        severe_threshold_reached = fail_streak >= JUMP_HOST_ADAPTIVE_SEVERE_FAIL_STREAK
        regular_threshold_reached = fail_streak >= JUMP_HOST_ADAPTIVE_FAIL_STREAK
        if current_slots > 1 and (regular_threshold_reached or (severe_saturation and severe_threshold_reached)):
            if severe_saturation and severe_threshold_reached:
                next_slots = 1 if JUMP_HOST_ADAPTIVE_SEVERE_DROP_TO_ONE else max(1, current_slots - 2)
                cooldown_seconds = JUMP_HOST_ADAPTIVE_SEVERE_COOLDOWN_SECONDS
                reason = "severe_saturation_streak"
            else:
                next_slots = max(1, current_slots - 1)
                cooldown_seconds = JUMP_HOST_ADAPTIVE_COOLDOWN_SECONDS
                reason = "saturation_streak"
            client.hset(slots_hash, jump_label, next_slots)
            client.setex(cooldown_key, cooldown_seconds, "1")
            client.delete(fail_streak_key)
            inc_counter(
                "jump_host_adaptive_slot_changes_total",
                labels={"jump_host": jump_label, "direction": "down"},
            )
            _emit_structured_event(
                "jump_host_slots_changed",
                jump_host=jump_label,
                direction="down",
                previous_slots=current_slots,
                new_slots=next_slots,
                reason=reason,
            )
    except Exception:
        logger.exception("Falha ao ajustar slots adaptativos (failure) para %s", jump_label)


def _record_jump_host_outcome(jump_label: str, success: bool) -> None:
    """Registra resultado (sucesso/falha) na janela deslizante do circuit breaker."""
    if not jump_label:
        return
    try:
        from app.services.realtime_backup_logs import get_redis_client
        client = get_redis_client()
        if not client:
            return
        now_ms = int(time.monotonic() * 1000)
        key = f"backup_center:cb:outcomes:{jump_label}"
        entry = f"{1 if success else 0}:{now_ms}"
        client.rpush(key, entry)
        client.expire(key, CIRCUIT_BREAKER_WINDOW_SECONDS + 60)
    except Exception:
        logger.exception("Falha ao registrar outcome do circuit breaker para %s", jump_label)


def _check_jump_host_circuit_breaker(jump_label: str) -> tuple[bool, str, int]:
    """Verifica se o circuit breaker do jump host está aberto.
    Retorna (is_open, reason, retry_after_seconds).
    """
    if not jump_label:
        return False, "", 0
    try:
        from app.services.realtime_backup_logs import get_redis_client
        client = get_redis_client()
        if not client:
            return False, "", 0

        open_key = f"backup_center:cb:open:{jump_label}"
        if client.get(open_key):
            ttl = max(0, int(client.ttl(open_key) or 0))
            return True, (
                f"Circuit breaker ABERTO para Jump Host {jump_label}. "
                f"Aguardando {ttl}s antes de novas tentativas."
            ), ttl

        outcomes_key = f"backup_center:cb:outcomes:{jump_label}"
        entries = client.lrange(outcomes_key, 0, -1)
        if not entries:
            return False, "", 0

        now_ms = int(time.monotonic() * 1000)
        cutoff_ms = now_ms - (CIRCUIT_BREAKER_WINDOW_SECONDS * 1000)
        successes = 0
        failures = 0
        valid_entries = []
        for raw in entries:
            try:
                parts = str(raw).split(":")
                outcome = int(parts[0])
                ts = int(parts[1])
                if ts >= cutoff_ms:
                    valid_entries.append(raw)
                    if outcome == 1:
                        successes += 1
                    else:
                        failures += 1
            except Exception:
                continue

        # Limpa entradas expiradas
        if len(valid_entries) < len(entries):
            try:
                pipe = client.pipeline()
                pipe.delete(outcomes_key)
                if valid_entries:
                    pipe.rpush(outcomes_key, *valid_entries)
                    pipe.expire(outcomes_key, CIRCUIT_BREAKER_WINDOW_SECONDS + 60)
                pipe.execute()
            except Exception:
                pass

        total = successes + failures
        if total < CIRCUIT_BREAKER_MIN_SAMPLES:
            return False, "", 0

        fail_pct = int((failures / total) * 100)
        if fail_pct >= CIRCUIT_BREAKER_THRESHOLD_PERCENT:
            client.setex(open_key, CIRCUIT_BREAKER_OPEN_SECONDS, "1")
            inc_counter(
                "jump_host_circuit_breaker_opened_total",
                labels={"jump_host": jump_label},
            )
            _emit_structured_event(
                "circuit_breaker_opened",
                jump_host=jump_label,
                fail_pct=fail_pct,
                total_samples=total,
                failures=failures,
                window_seconds=CIRCUIT_BREAKER_WINDOW_SECONDS,
                open_seconds=CIRCUIT_BREAKER_OPEN_SECONDS,
            )
            return True, (
                f"Circuit breaker ATIVADO para Jump Host {jump_label}: "
                f"{fail_pct}% de falhas ({failures}/{total}) na janela de {CIRCUIT_BREAKER_WINDOW_SECONDS}s. "
                f"Bloqueado por {CIRCUIT_BREAKER_OPEN_SECONDS}s."
            ), int(CIRCUIT_BREAKER_OPEN_SECONDS)
        return False, "", 0
    except Exception:
        logger.exception("Falha ao verificar circuit breaker para %s", jump_label)
        return False, "", 0


def _resolve_device_observability_context(device_id: str) -> dict:
    from app.models.device import Device
    from app.models.device_type import DeviceType

    db = SessionLocal()
    try:
        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            return {
                "device_id": str(device_id),
                "device_name": "unknown",
                "tenant_id": "unknown",
                "device_type": "unknown",
                "script_name": "unknown",
                "group_name": "unknown",
                "target_host": "unknown",
                "target_port": "0",
                "jump_host": "direct",
            }
        device_type_name = "unknown"
        script_name = "unknown"
        if getattr(device, "device_type_id", None):
            row = db.query(DeviceType.name, DeviceType.script_name).filter(DeviceType.id == device.device_type_id).first()
            if row:
                if row[0]:
                    device_type_name = str(row[0]).strip() or "unknown"
                if row[1]:
                    script_name = str(row[1]).strip() or "unknown"
        jump_host_label = "direct"
        group = getattr(device, "group", None)
        group_name = str(getattr(group, "name", "") or "Sem grupo")
        if group and uses_jump_host(group, device=device) and getattr(group, "jump_host", None):
            jump_host_label = f"{group.jump_host}:{int(getattr(group, 'jump_port', 22) or 22)}"
        return {
            "device_id": str(device_id),
            "device_name": str(getattr(device, "name", "") or ""),
            "tenant_id": str(getattr(device, "tenant_id", "unknown") or "unknown"),
            "device_type": device_type_name,
            "script_name": script_name,
            "group_name": group_name,
            "target_host": str(getattr(device, "ip_address", "") or ""),
            "target_port": str(int(getattr(device, "port", 22) or 22)),
            "jump_host": jump_host_label,
        }
    except Exception:
        logger.exception("Falha ao resolver contexto de observabilidade para %s", device_id)
        return {
            "device_id": str(device_id),
            "device_name": "unknown",
            "tenant_id": "unknown",
            "device_type": "unknown",
            "script_name": "unknown",
            "group_name": "unknown",
            "target_host": "unknown",
            "target_port": "0",
            "jump_host": "unknown",
        }
    finally:
        db.close()


def _validate_device_execution_allowed(device_id: str) -> tuple[bool, str | None, str | None]:
    """
    Garante que o dispositivo pode executar backup.
    Regras:
    - Dispositivo deve existir e estar ativo.
    - Se possuir grupo, o grupo precisa estar ativo.
    """
    from app.models.device import Device

    db = SessionLocal()
    try:
        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            return False, "Dispositivo nao encontrado.", "device_not_found"
        if not bool(getattr(device, "is_active", True)):
            return False, "Dispositivo inativo. Backup bloqueado.", "device_inactive"
        group = getattr(device, "group", None)
        if group is not None and not bool(getattr(group, "is_active", True)):
            group_name = str(getattr(group, "name", "") or "Sem grupo")
            return (
                False,
                f'Grupo "{group_name}" esta inativo. Backup bloqueado.',
                "group_inactive",
            )
        return True, None, None
    except Exception:
        logger.exception("Falha ao validar elegibilidade de execucao para %s", device_id)
        return False, "Falha ao validar estado do dispositivo/grupo.", "validation_error"
    finally:
        db.close()


def _normalize_host(value: str | None) -> str:
    return str(value or "").strip().lower()


def _resolve_jump_host_lock(device_id: str) -> dict | None:
    from app.models.device import Device

    db = SessionLocal()
    try:
        device = db.query(Device).filter(Device.id == device_id).first()
        if not device or not device.group or not uses_jump_host(device.group, device=device):
            return None

        jump_host = str(getattr(device.group, "jump_host", "") or "").strip()
        if not jump_host:
            return None

        jump_port = int(getattr(device.group, "jump_port", 22) or 22)
        target_host = _normalize_host(getattr(device, "ip_address", None))
        jump_host_normalized = _normalize_host(jump_host)
        if target_host and target_host == jump_host_normalized:
            return None

        group_name = str(getattr(device.group, "name", "") or "Sem grupo").strip()
        return {
            "base_key": f"backup_center:jump_host_lock:{jump_host_normalized}:{jump_port}",
            "label": f"{jump_host}:{jump_port}",
            "group_name": group_name,
            "base_slots": JUMP_HOST_MAX_SLOTS,
            "max_slots": JUMP_HOST_MAX_SLOTS,
        }
    except Exception:
        logger.exception("Falha ao resolver lock de Jump Host para %s", device_id)
        return None
    finally:
        db.close()


@contextmanager
def _jump_host_lock_context(device_id: str, task_id: str | None = None, bulk_task_id: str | None = None):
    from app.services.realtime_backup_logs import append_task_log, get_redis_client

    lock_info = _resolve_jump_host_lock(device_id)
    if not lock_info:
        yield None
        return

    client = get_redis_client()
    if not client:
        logger.warning(
            "Redis indisponivel; seguindo sem lock de Jump Host para %s (%s).",
            device_id,
            lock_info["label"],
        )
        yield {**lock_info, "max_slots": lock_info.get("base_slots") or 1}
        return

    base_slots = max(1, int(lock_info.get("base_slots") or lock_info.get("max_slots") or 1))
    max_slots = _resolve_effective_jump_host_slots(client, str(lock_info.get("label") or ""), base_slots)
    slot_locks = [
        client.lock(
            f"{lock_info['base_key']}:slot:{idx}",
            timeout=JUMP_HOST_LOCK_TIMEOUT_SECONDS,
            blocking_timeout=1,
            sleep=1.0,
        )
        for idx in range(max_slots)
    ]
    waiting_logged = False
    acquired_lock = None
    acquired_slot = None
    effective_wait_seconds = _effective_jump_host_wait_seconds(bulk_task_id)

    wait_started_at = time.monotonic()
    try:
        while True:
            if _should_stop_now(bulk_task_id):
                wait_elapsed = max(0.0, time.monotonic() - wait_started_at)
                observe_histogram(
                    "jump_host_wait_seconds",
                    wait_elapsed,
                    labels={"jump_host": lock_info["label"], "result": "cancelled"},
                )
                inc_counter(
                    "jump_host_slot_acquire_total",
                    labels={"jump_host": lock_info["label"], "result": "cancelled", "slots": str(max_slots)},
                )
                raise JumpHostSlotCancelledError(
                    f"Execucao interrompida enquanto aguardava slot do Jump Host {lock_info['label']}."
                )
            if (time.monotonic() - wait_started_at) >= effective_wait_seconds:
                wait_elapsed = max(0.0, time.monotonic() - wait_started_at)
                observe_histogram(
                    "jump_host_wait_seconds",
                    wait_elapsed,
                    labels={"jump_host": lock_info["label"], "result": "timeout"},
                )
                inc_counter(
                    "jump_host_slot_acquire_total",
                    labels={"jump_host": lock_info["label"], "result": "timeout", "slots": str(max_slots)},
                )
                raise JumpHostSlotTimeoutError(
                    (
                        f"Timeout aguardando slot do Jump Host {lock_info['label']} "
                        f"(limite {max_slots} conexoes simultaneas) "
                        f"apos {effective_wait_seconds}s."
                    )
                )

            for slot_idx, slot_lock in enumerate(slot_locks, start=1):
                try:
                    if slot_lock.acquire(blocking=False):
                        acquired_lock = slot_lock
                        acquired_slot = slot_idx
                        break
                except Exception:
                    logger.exception(
                        "Falha ao tentar adquirir slot %s do Jump Host %s",
                        slot_idx,
                        lock_info["label"],
                    )

            if acquired_lock is not None:
                wait_elapsed = max(0.0, time.monotonic() - wait_started_at)
                observe_histogram(
                    "jump_host_wait_seconds",
                    wait_elapsed,
                    labels={"jump_host": lock_info["label"], "result": "acquired"},
                )
                inc_counter(
                    "jump_host_slot_acquire_total",
                    labels={"jump_host": lock_info["label"], "result": "acquired", "slots": str(max_slots)},
                )
                if waiting_logged:
                    append_task_log(
                        task_id,
                        "Sistema",
                        (
                            f"Slot {acquired_slot}/{max_slots} liberado no Jump Host {lock_info['label']}. "
                            "Prosseguindo com o backup."
                        ),
                        "info",
                    )
                yield {**lock_info, "slot": acquired_slot, "max_slots": max_slots, "base_slots": base_slots}
                return

            if not waiting_logged:
                append_task_log(
                    task_id,
                    "Sistema",
                    (
                        f"Jump Host compartilhado detectado ({lock_info['label']}). "
                        f"Aguardando slot livre (max {max_slots} conexoes simultaneas) para evitar excesso de canais SSH."
                    ),
                    "info",
                )
                waiting_logged = True

            if _sleep_with_stop_poll(1, bulk_task_id):
                wait_elapsed = max(0.0, time.monotonic() - wait_started_at)
                observe_histogram(
                    "jump_host_wait_seconds",
                    wait_elapsed,
                    labels={"jump_host": lock_info["label"], "result": "cancelled"},
                )
                inc_counter(
                    "jump_host_slot_acquire_total",
                    labels={"jump_host": lock_info["label"], "result": "cancelled", "slots": str(max_slots)},
                )
                raise JumpHostSlotCancelledError(
                    f"Execucao interrompida enquanto aguardava slot do Jump Host {lock_info['label']}."
                )
    finally:
        try:
            if acquired_lock and acquired_lock.owned():
                acquired_lock.release()
        except Exception:
            logger.exception("Falha ao liberar lock de Jump Host %s", lock_info["label"])


def _multi_device_progress(total_devices: int, processed_devices: int, current_fraction: float = 0.0) -> int:
    total = max(1, int(total_devices or 1))
    processed = max(0.0, float(processed_devices or 0))
    fraction = max(0.0, min(0.99, float(current_fraction or 0.0)))
    # Reserva 5% para bootstrap e 5% para finalizacao.
    return min(95, max(5, int(5 + (((processed + fraction) / total) * 90))))


from celery.exceptions import SoftTimeLimitExceeded

@celery_app.task(
    bind=True,
    max_retries=0,
    time_limit=BACKUP_TASK_TIME_LIMIT_SECONDS,
    soft_time_limit=BACKUP_TASK_SOFT_TIME_LIMIT_SECONDS,
)
def run_backup_task(self, device_id: str, bulk_task_id: str = None):
    """
    Task assíncrona wrapper com Time Limit rigoroso.
    """
    try:
        return _internal_run_backup_task(self, device_id, bulk_task_id)
    except SoftTimeLimitExceeded:
        import logging
        from app.services.realtime_backup_logs import append_task_log, update_task_meta
        logger = logging.getLogger(__name__)
        logger.error(f"[Device {device_id}] Celery Time Limit excedido (sessão congelada).")
        update_task_meta(
            self.request.id,
            status="failed",
            message=(
                "Timeout absoluto de socket/Worker atingido "
                f"({BACKUP_TASK_SOFT_TIME_LIMIT_SECONDS}s)."
            ),
            completed=True,
            error="SoftTimeLimitExceeded."
        )
        append_task_log(self.request.id, device_id, "Processo interrompido à força pelo limite de tempo do sistema. Possível timeout silencioso no equipamento originado.", "error")
        return {
            "success": False,
            "error": f"Timeout absoluto ({BACKUP_TASK_SOFT_TIME_LIMIT_SECONDS}s) atingido.",
        }

def _internal_run_backup_task(self, device_id: str, bulk_task_id: str = None):
    """
    Task assíncrona para executar backup de um dispositivo.
    
    Args:
        device_id: UUID do dispositivo
    
    Returns:
        Dict com resultado do backup
    """
    from app.services.backup_executor import backup_executor
    from app.services.realtime_backup_logs import append_task_log, update_task_meta
    from app.services.backup_diagnostics import classify_failure

    task_id = self.request.id
    started_at = time.monotonic()
    observability_ctx = _resolve_device_observability_context(device_id)
    device_name_hint = str(observability_ctx.get("device_name") or "").strip()
    base_metric_labels = {
        "tenant_id": str(observability_ctx.get("tenant_id") or "unknown"),
        "device_type": str(observability_ctx.get("device_type") or "unknown"),
        "script_name": str(observability_ctx.get("script_name") or "unknown"),
        "group_name": str(observability_ctx.get("group_name") or "unknown"),
        "target_host": str(observability_ctx.get("target_host") or "unknown"),
        "target_port": str(observability_ctx.get("target_port") or "0"),
        "jump_host": str(observability_ctx.get("jump_host") or "unknown"),
    }
    attempt_no = int(self.request.retries or 0) + 1
    lock_info = None

    def _track_attempt_outcome(outcome: str, category: str, message: str | None, *, retry_scheduled: bool = False) -> None:
        duration = max(0.0, time.monotonic() - started_at)
        metric_labels = {
            **base_metric_labels,
            "outcome": str(outcome or "unknown"),
            "category": str(category or "none"),
        }
        observe_histogram("backup_task_duration_seconds", duration, labels=metric_labels)
        inc_counter("backup_task_total", labels=metric_labels)
        if retry_scheduled:
            inc_counter(
                "backup_retry_total",
                labels={
                    **base_metric_labels,
                    "category": str(category or "none"),
                    "reason": str(outcome or "retry"),
                },
            )
        _emit_structured_event(
            "backup_task_attempt_finished",
            task_id=str(task_id),
            bulk_task_id=str(bulk_task_id) if bulk_task_id else None,
            device_id=str(device_id),
            tenant_id=base_metric_labels["tenant_id"],
            device_type=base_metric_labels["device_type"],
            script_name=base_metric_labels["script_name"],
            attempt=attempt_no,
            outcome=str(outcome or "unknown"),
            category=str(category or "none"),
            duration_ms=int(duration * 1000),
            retries_done=int(self.request.retries or 0),
            max_retries=int(self.max_retries or 0),
            jump_host=base_metric_labels["jump_host"],
            group_name=base_metric_labels["group_name"],
            target_host=base_metric_labels["target_host"],
            target_port=base_metric_labels["target_port"],
            message=(str(message or "")[:500] or None),
        )
        _touch_bulk_activity(bulk_task_id)

    _emit_structured_event(
        "backup_task_attempt_started",
        task_id=str(task_id),
        bulk_task_id=str(bulk_task_id) if bulk_task_id else None,
        device_id=str(device_id),
        tenant_id=base_metric_labels["tenant_id"],
        device_type=base_metric_labels["device_type"],
        script_name=base_metric_labels["script_name"],
        attempt=attempt_no,
        retries_done=int(self.request.retries or 0),
        max_retries=int(self.max_retries or 0),
        jump_host=base_metric_labels["jump_host"],
        group_name=base_metric_labels["group_name"],
        target_host=base_metric_labels["target_host"],
        target_port=base_metric_labels["target_port"],
    )

    try:
        _touch_bulk_activity(bulk_task_id)
        if _should_stop_now(bulk_task_id):
            cancelled_result = {
                'device_id': device_id,
                'success': False,
                'message': 'Backup interrompido pelo operador (parada forçada).'
            }
            update_task_meta(
                task_id,
                status="stopped",
                progress=100,
                message="Execucao interrompida por parada forçada.",
                completed=True,
                result=cancelled_result,
            )
            append_task_log(task_id, "Sistema", "Task interrompida por parada forçada.", "warning")
            _track_attempt_outcome(
                outcome="stopped",
                category="cancelled",
                message=cancelled_result["message"],
            )
            return cancelled_result

        allowed, block_message, block_category = _validate_device_execution_allowed(device_id)
        if not allowed:
            blocked_result = {
                "device_id": device_id,
                "success": False,
                "message": block_message or "Execucao bloqueada por politica de estado.",
                "failure_category": block_category or "blocked",
            }
            update_task_meta(
                task_id,
                status="failed",
                progress=100,
                message=blocked_result["message"],
                completed=True,
                result=blocked_result,
            )
            append_task_log(task_id, "Sistema", blocked_result["message"], "warning")
            _track_attempt_outcome(
                outcome="failed",
                category=blocked_result["failure_category"],
                message=blocked_result["message"],
            )
            return blocked_result

        logger.info(f"Iniciando backup do dispositivo {device_id}")
        update_task_meta(
            task_id,
            status="running",
            progress=10,
            message="Iniciando conexao com o dispositivo...",
            completed=False,
        )
        append_task_log(task_id, "Sistema", f"Backup iniciado para dispositivo {device_id}", "info")

        # Circuit breaker: verificar se o jump host está bloqueado
        cb_jump_label = str(base_metric_labels.get("jump_host") or "direct")
        if cb_jump_label != "direct":
            cb_open, cb_reason, cb_retry_after = _check_jump_host_circuit_breaker(cb_jump_label)
            if cb_open:
                cb_result = {
                    'device_id': device_id,
                    'success': False,
                    'message': cb_reason,
                    'failure_category': 'circuit_breaker',
                }
                update_task_meta(
                    task_id, status="failed", progress=100,
                    message=cb_reason, completed=True, result=cb_result,
                )
                append_task_log(task_id, "Sistema", cb_reason, "warning")
                _track_attempt_outcome(
                    outcome="circuit_breaker", category="circuit_breaker", message=cb_reason,
                )
                return cb_result

        with _jump_host_lock_context(device_id, task_id=task_id, bulk_task_id=bulk_task_id) as lock_ctx:
            lock_info = lock_ctx
            success, message = backup_executor.run_backup_for_device_id(device_id, task_id=task_id)
        message = (message or "").strip()
        if not success and not message:
            message = "Falha sem mensagem retornada pelo executor."
        failure_category = classify_failure(message) if not success else None
        
        result = {
            'device_id': device_id,
            'success': success,
            'message': message,
            'failure_category': failure_category,
        }
        
        if success:
            logger.info(f"Backup concluído com sucesso: {device_id}")
            _adapt_jump_host_slots(lock_info, True, message)
            if cb_jump_label != "direct":
                _record_jump_host_outcome(cb_jump_label, True)
            update_task_meta(
                task_id,
                status="success",
                progress=100,
                message=message or "Backup concluido com sucesso.",
                completed=True,
                result=result,
            )
            append_task_log(task_id, "Sistema", "Backup finalizado com sucesso.", "success")
            _track_attempt_outcome(
                outcome="success",
                category="none",
                message=message,
            )
        else:
            _adapt_jump_host_slots(lock_info, False, message)
            if cb_jump_label != "direct":
                # Circuit breaker do Jump Host deve refletir apenas falhas de TRANSPORTE/JH.
                # Falhas de credencial do dispositivo (auth/InvalidToken/script) nao devem
                # contaminar a saude do bastion e abrir CB em cascata.
                if failure_category == "banner_timeout":
                    _record_jump_host_outcome(cb_jump_label, True)
                elif failure_category in CB_FAILURE_CATEGORIES:
                    _record_jump_host_outcome(cb_jump_label, False)

            logger.warning(f"Backup falhou: {device_id} - {message}")
            update_task_meta(
                task_id,
                status="failed",
                progress=100,
                message=message or "Backup finalizado com falha.",
                completed=True,
                result=result,
            )
            append_task_log(task_id, "Sistema", "Backup finalizado com falha.", "error")
            _track_attempt_outcome(
                outcome="failed",
                category=failure_category or "failure",
                message=message,
            )
        
        return result
    except Retry:
        raise
    except JumpHostSlotCancelledError as e:
        logger.warning("Backup interrompido aguardando slot do Jump Host: %s", e)
        result = {
            'device_id': device_id,
            'success': False,
            'message': str(e),
            'failure_category': 'cancelled',
        }
        update_task_meta(
            task_id,
            status="stopped",
            progress=100,
            message=str(e),
            completed=True,
            result=result,
        )
        append_task_log(task_id, "Sistema", str(e), "warning")
        _track_attempt_outcome(
            outcome="stopped",
            category="jump_host_slot_cancelled",
            message=str(e),
        )
        return result
    except JumpHostSlotTimeoutError as e:
        logger.warning("Timeout aguardando slot do Jump Host: %s", e)
        if not lock_info:
            lock_info = _resolve_jump_host_lock(device_id)
        _adapt_jump_host_slots(lock_info, False, str(e))
        result = {
            'device_id': device_id,
            'success': False,
            'message': str(e),
            'failure_category': 'jump_host_slot_timeout',
        }
        update_task_meta(
            task_id,
            status="failed",
            progress=100,
            message=str(e),
            completed=True,
            result=result,
        )
        append_task_log(task_id, "Sistema", str(e), "error")
        _track_attempt_outcome(
            outcome="failed",
            category="jump_host_slot_timeout",
            message=str(e),
        )
        return result
    except Exception as e:
        error_text = _safe_error_text(e)
        logger.error(f"Erro ao executar backup {device_id}: {error_text}")
        if not lock_info:
            lock_info = _resolve_jump_host_lock(device_id)
        _adapt_jump_host_slots(lock_info, False, error_text)
        failure_category = classify_failure(error_text) or "task_exception"

        task_error_result = {
            'device_id': device_id,
            'success': False,
            'message': f"Erro na task: {error_text}",
            'failure_category': failure_category,
        }
        update_task_meta(
            task_id, status="failed", progress=100,
            message=f"Erro na task: {error_text}", completed=True, result=task_error_result,
        )
        append_task_log(task_id, "Sistema", f"Erro na task ({failure_category}): {error_text}", "error")
        _track_attempt_outcome(
            outcome="failed", category=failure_category, message=error_text,
        )
        return task_error_result


@celery_app.task(bind=True)
def enqueue_jump_and_vpn_after_direct_phase_task(
    self,
    direct_phase_results,
    tenant_id: str,
    jump_groups_payload=None,
    vpn_groups_payload=None,
    bulk_task_id: str = None,
):
    """
    Callback da fase direta do backup em massa.
    Fase 2: enfileira grupos Jump Host (por grupo).
    Fase 3: enfileira grupos VPN apenas após concluir a fase Jump.
    """
    from celery import chord
    from app.services.realtime_backup_logs import (
        append_task_log,
        get_task_meta,
        register_task,
        update_task_meta,
    )
    import uuid

    jump_groups_payload = jump_groups_payload or []
    vpn_groups_payload = vpn_groups_payload or []
    tenant_id = str(tenant_id)
    task_id = self.request.id

    if not jump_groups_payload:
        # Sem fase Jump: delega direto para callback já existente da fase VPN.
        return enqueue_vpn_groups_after_direct_phase_task.run(
            direct_phase_results,
            tenant_id,
            vpn_groups_payload,
            bulk_task_id,
        )

    if _should_stop_now(bulk_task_id):
        if bulk_task_id:
            append_task_log(
                bulk_task_id,
                "Backup em massa",
                "Parada solicitada. Grupos Jump/VPN nao foram enfileirados apos a fase direta.",
                "warning",
            )
            update_task_meta(
                bulk_task_id,
                status="stopping",
                message="Parada solicitada. Fases Jump/VPN nao serao iniciadas.",
            )
        return {"queued_jump_groups": 0, "queued_vpn_groups": 0, "task_ids": [], "stopped": True}

    queued_jump = 0
    new_task_ids = []
    new_task_device_count = {}
    jump_signatures = []

    for group_idx, item in enumerate(jump_groups_payload, start=1):
        group_id = str((item or {}).get("group_id") or "").strip()
        group_name = str((item or {}).get("group_name") or group_id or "Grupo Jump Host").strip()
        device_ids = sorted(set((item or {}).get("device_ids") or []))
        if not group_id or not device_ids:
            continue

        jump_task_id = str(uuid.uuid4())
        jump_args = [group_id, tenant_id, device_ids]
        if bulk_task_id:
            jump_args.append(bulk_task_id)
        sig = run_vpn_group_backups_task.s(*jump_args).set(
            task_id=jump_task_id,
            queue="jump_queue",
            countdown=max(0, (group_idx - 1) * JUMP_PHASE_GROUP_STAGGER_SECONDS),
        )
        jump_signatures.append(sig)
        queued_jump += 1
        new_task_ids.append(jump_task_id)
        new_task_device_count[jump_task_id] = len(device_ids)

        if bulk_task_id:
            register_task(
                task_id=jump_task_id,
                tenant_id=tenant_id,
                device_name=f"Grupo Jump Host {group_name}",
                group_id=group_id,
            )

    if not jump_signatures:
        # Nada para Jump: segue direto para VPN.
        if vpn_groups_payload:
            return enqueue_vpn_groups_after_direct_phase_task.run(
                direct_phase_results,
                tenant_id,
                vpn_groups_payload,
                bulk_task_id,
            )
        return {"queued_jump_groups": 0, "queued_vpn_groups": 0, "task_ids": []}

    if bulk_task_id:
        current = get_task_meta(bulk_task_id) or {}
        current_child_ids = [str(tid) for tid in (current.get("child_task_ids") or []) if tid]
        merged_child_ids = list(dict.fromkeys(current_child_ids + new_task_ids))

        child_count = current.get("child_task_device_count") or {}
        if not isinstance(child_count, dict):
            child_count = {}
        for k, v in new_task_device_count.items():
            child_count[str(k)] = int(v)

        total_tasks = int(current.get("total_tasks") or 0)
        pending_vpn = len(vpn_groups_payload) if vpn_groups_payload else 0
        min_expected = len(merged_child_ids) + pending_vpn
        if total_tasks < min_expected:
            total_tasks = min_expected

        update_task_meta(
            bulk_task_id,
            child_task_ids=merged_child_ids,
            child_task_device_count=child_count,
            total_tasks=total_tasks,
            status="running",
            message=(
                f"Fase Jump Host iniciada com {queued_jump} grupo(s). "
                f"Fase VPN pendente: {pending_vpn} grupo(s)."
            ),
        )
        append_task_log(
            bulk_task_id,
            "Backup em massa",
            (
                f"Fase 2 (Jump Host) enfileirada com {queued_jump} grupo(s). "
                f"Fase 3 (VPN): {len(vpn_groups_payload)} grupo(s) aguardando callback."
            ),
            "info",
        )

    if vpn_groups_payload:
        callback_sig = enqueue_vpn_groups_after_direct_phase_task.s(
            tenant_id,
            vpn_groups_payload,
            bulk_task_id,
        ).set(queue="jump_queue")
        chord(jump_signatures)(callback_sig)
    else:
        for sig in jump_signatures:
            sig.apply_async()

    append_task_log(
        task_id,
        "Sistema",
        (
            f"Callback da fase direta finalizado. Grupos Jump enfileirados: {queued_jump}. "
            f"Grupos VPN pendentes: {len(vpn_groups_payload)}."
        ),
        "info",
    )
    return {
        "queued_jump_groups": queued_jump,
        "queued_vpn_groups": len(vpn_groups_payload),
        "task_ids": new_task_ids,
    }


@celery_app.task(bind=True)
def enqueue_vpn_groups_after_direct_phase_task(
    self,
    direct_phase_results,
    tenant_id: str,
    vpn_groups_payload=None,
    bulk_task_id: str = None,
):
    """
    Callback da fase direta do backup em massa.
    Só enfileira grupos VPN após concluir os dispositivos sem VPN.
    """
    from app.services.realtime_backup_logs import (
        append_task_log,
        get_task_meta,
        register_task,
        update_task_meta,
    )

    vpn_groups_payload = vpn_groups_payload or []
    tenant_id = str(tenant_id)
    task_id = self.request.id

    if not vpn_groups_payload:
        if bulk_task_id:
            append_task_log(
                bulk_task_id,
                "Backup em massa",
                "Fase direta concluida. Nenhum grupo VPN pendente.",
                "info",
            )
        return {"queued_vpn_groups": 0, "task_ids": []}

    if _should_stop_now(bulk_task_id):
        if bulk_task_id:
            append_task_log(
                bulk_task_id,
                "Backup em massa",
                "Parada solicitada. Grupos VPN nao foram enfileirados apos a fase direta.",
                "warning",
            )
            update_task_meta(
                bulk_task_id,
                status="stopping",
                message="Parada solicitada. Fase VPN nao sera iniciada.",
            )
        return {"queued_vpn_groups": 0, "task_ids": [], "stopped": True}

    queued = 0
    new_task_ids = []
    new_task_device_count = {}

    for item in vpn_groups_payload:
        group_id = str((item or {}).get("group_id") or "").strip()
        device_ids = sorted(set((item or {}).get("device_ids") or []))
        if not group_id or not device_ids:
            continue

        vpn_args = [group_id, tenant_id, device_ids]
        force_vpn = bool((item or {}).get("force_vpn"))
        if bulk_task_id:
            vpn_args.append(bulk_task_id)
        task = run_vpn_group_backups_task.apply_async(
            args=vpn_args,
            kwargs={"force_vpn": force_vpn},
            queue="vpn_queue",
        )
        queued += 1
        task_id_str = str(task.id)
        new_task_ids.append(task_id_str)
        new_task_device_count[task_id_str] = len(device_ids)

        if bulk_task_id:
            register_task(
                task_id=task_id_str,
                tenant_id=tenant_id,
                device_name=f"Grupo VPN {group_id}",
                group_id=group_id,
            )

    if bulk_task_id:
        current = get_task_meta(bulk_task_id) or {}
        current_child_ids = [str(tid) for tid in (current.get("child_task_ids") or []) if tid]
        merged_child_ids = list(dict.fromkeys(current_child_ids + new_task_ids))

        child_count = current.get("child_task_device_count") or {}
        if not isinstance(child_count, dict):
            child_count = {}
        for k, v in new_task_device_count.items():
            child_count[str(k)] = int(v)

        total_tasks = int(current.get("total_tasks") or 0)
        if total_tasks < len(merged_child_ids):
            total_tasks = len(merged_child_ids)

        update_task_meta(
            bulk_task_id,
            child_task_ids=merged_child_ids,
            child_task_device_count=child_count,
            total_tasks=total_tasks,
            status="running",
            message=(
                f"Fase direta concluida. {queued} grupo(s) VPN enfileirado(s) "
                "para a fase final."
            ),
        )
        append_task_log(
            bulk_task_id,
            "Backup em massa",
            (
                f"Fase direta concluida. Enfileirado(s) {queued} grupo(s) VPN "
                "somente apos finalizar os dispositivos sem VPN."
            ),
            "info",
        )

    append_task_log(
        task_id,
        "Sistema",
        f"Callback da fase direta finalizado. Grupos VPN enfileirados: {queued}.",
        "info",
    )
    return {"queued_vpn_groups": queued, "task_ids": new_task_ids}


@celery_app.task(bind=True)
def run_backup_group_task(self, group_id: str, tenant_id: str):
    """
    Task assíncrona para executar backup de todos os dispositivos de um grupo.
    
    Args:
        group_id: UUID do grupo
        tenant_id: UUID do tenant
    
    Returns:
        Dict com resumo dos resultados
    """
    from app.models.device import Device
    from app.models.device_group import DeviceGroup
    import uuid
    
    db = SessionLocal()
    
    try:
        logger.info(f"Iniciando backup em massa do grupo {group_id}")
        group_uuid = uuid.UUID(group_id)
        group = db.query(DeviceGroup).filter(
            DeviceGroup.id == group_uuid,
            DeviceGroup.tenant_id == tenant_id
        ).first()
        if not group:
            return {'error': f'Grupo {group_id} não encontrado para o tenant informado.'}
        if not bool(getattr(group, "is_active", True)):
            logger.info("Grupo %s inativo; backup de grupo ignorado.", group_id)
            return {
                'group_id': group_id,
                'total': 0,
                'success': 0,
                'failed': 0,
                'skipped': 0,
                'details': [],
                'message': f'Grupo {group.name} inativo. Execucao ignorada.',
            }

        devices = db.query(Device).filter(
            Device.group_id == group_uuid,
            Device.tenant_id == tenant_id,
            Device.is_active == True
        ).all()
        
        results = {
            'group_id': group_id,
            'total': len(devices),
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'details': []
        }
        
        scheduled_devices = [d for d in devices if d.backup_scheduled]
        if uses_vpn_tunnel(group) and scheduled_devices:
            task = run_vpn_group_backups_task.apply_async(
                args=[group_id, tenant_id, [str(d.id) for d in scheduled_devices]],
                queue='vpn_queue'
            )
            results['details'].append({
                'group_name': group.name,
                'task_id': task.id,
                'mode': 'vpn_group'
            })
            logger.info(
                "Grupo %s enfileirado na vpn_queue (%s dispositivos)",
                group_id, len(scheduled_devices)
            )
            return results

        for device in scheduled_devices:
            # Roteia por tipo de conexao para isolar concorrencia:
            # - VPN -> vpn_queue
            # - Jump Host -> jump_queue
            # - Direto -> celery
            if uses_vpn_tunnel(group, device=device):
                target_queue = 'vpn_queue'
            elif uses_jump_host(group, device=device):
                target_queue = 'jump_queue'
            else:
                target_queue = 'celery'
            task = run_backup_task.apply_async(args=[str(device.id)], queue=target_queue)
            results['details'].append({
                'device_id': str(device.id),
                'device_name': device.name,
                'task_id': task.id,
                'queue': target_queue,
            })
        
        logger.info(f"Grupo {group_id}: {len(results['details'])} backups enfileirados")
        return results
    except Exception as e:
        logger.error(f"Erro no backup do grupo {group_id}: {e}")
        return {'error': str(e)}
    finally:
        db.close()


@celery_app.task(bind=True, queue='vpn_queue')
def run_vpn_group_backups_task(
    self,
    group_id: str,
    tenant_id: str,
    device_ids=None,
    bulk_task_id: str = None,
    force_vpn: bool = False,
):
    """
    Executa backups de um grupo VPN em sessão única:
    conecta VPN -> executa backups -> desconecta VPN.
    """
    from app.models.device import Device
    from app.models.device_group import DeviceGroup
    from app.services.backup_executor import backup_executor
    from app.services.vpn_service import vpn_service, VpnError
    from app.services.realtime_backup_logs import append_task_log, update_task_meta
    from app.services.backup_diagnostics import classify_failure
    import uuid

    from sqlalchemy.orm import joinedload

    db = SessionLocal()
    device_ids = device_ids or []
    task_id = self.request.id

    try:
        _touch_bulk_activity(bulk_task_id)
        group_uuid = uuid.UUID(group_id)
        group = db.query(DeviceGroup).filter(
            DeviceGroup.id == group_uuid,
            DeviceGroup.tenant_id == tenant_id
        ).first()
        if not group:
            update_task_meta(
                task_id,
                status="failed",
                progress=100,
                message=f"Grupo {group_id} nao encontrado.",
                completed=True,
                error=f"Grupo {group_id} nao encontrado.",
            )
            return {'error': f'Grupo {group_id} não encontrado.'}
        if not bool(getattr(group, "is_active", True)):
            inactive_msg = f'Grupo {group.name} inativo. Execucao VPN bloqueada.'
            update_task_meta(
                task_id,
                status="failed",
                progress=100,
                message=inactive_msg,
                completed=True,
                error=inactive_msg,
            )
            append_task_log(task_id, group.name, inactive_msg, "warning")
            return {'error': inactive_msg, 'group_id': group_id}

        query = db.query(Device).options(
            joinedload(Device.group),
            joinedload(Device.subgroup),
            joinedload(Device.type),
        ).filter(
            Device.tenant_id == tenant_id,
            Device.group_id == group_uuid,
            Device.is_active == True,
            Device.backup_scheduled == True
        )
        if device_ids:
            query = query.filter(Device.id.in_(device_ids))
        devices = query.all()
        
        # Touch relationships to ensure they are cached locally on the object,
        # especially for None values which might otherwise trigger lazy loads.
        # Tambem cacheia o modo de conexao efetivo por dispositivo antes de
        # desanexar a sessao (evita qualquer lazy-load posterior de subgroup).
        device_connection_flags = {}
        for d in devices:
            _ = d.group
            _ = d.subgroup
            _ = d.type
            device_connection_flags[str(d.id)] = {
                "vpn": bool(uses_vpn_tunnel(group, device=d)),
                "jump": bool(uses_jump_host(group, device=d)),
            }

        # Evita manter conexao/transacao de DB aberta durante tentativas longas de VPN/L2TP.
        # expunge_all() desanexa os objetos da sessao, mantendo os atributos eager-loaded
        # (group, subgroup, type) acessiveis em memoria sem tentar lazy load.
        db.expunge_all()
        db.close()

        result = {
            'group_id': group_id,
            'group_name': group.name,
            'mode': 'vpn_group',
            'total': len(devices),
            'success': 0,
            'failed': 0,
            'details': []
        }

        if not devices:
            update_task_meta(
                task_id,
                status="success",
                progress=100,
                message="Nenhum dispositivo elegivel para backup.",
                completed=True,
                result=result,
            )
            return result

        if _should_stop_now(bulk_task_id):
            result["failed"] = len(devices)
            result["details"].append({
                "device_id": None,
                "device_name": "Lote",
                "success": False,
                "message": "Interrompido por parada forçada antes do inicio da execucao."
            })
            update_task_meta(
                task_id,
                status="stopped",
                progress=100,
                message=f"Grupo {group.name} interrompido por parada forçada.",
                completed=True,
                result=result,
            )
            append_task_log(task_id, group.name, "Execucao do grupo interrompida por parada forçada.", "warning")
            return result

        update_task_meta(
            task_id,
            status="running",
            progress=5,
            message=f"Iniciando backup via VPN para grupo {group.name}...",
            completed=False,
            total_devices=len(devices),
            processed_devices=0,
            done_devices=0,
            success_devices=0,
            failed_devices=0,
            current_device_name=None,
            current_device_index=0,
            current_device_fraction=0.0,
        )
        append_task_log(task_id, group.name, "Iniciando workflow VPN do grupo.", "info")
        if force_vpn:
            append_task_log(task_id, group.name, "Modo VPN forçado por subgrupo de conexão.", "info")
        _touch_bulk_activity(bulk_task_id)

        vpn_required_devices = []
        if force_vpn:
            vpn_required_devices = list(devices)
        else:
            vpn_required_devices = [
                device
                for device in devices
                if bool((device_connection_flags.get(str(device.id)) or {}).get("vpn"))
            ]
        use_vpn_mode = bool(vpn_required_devices)
        if force_vpn:
            append_task_log(
                task_id,
                group.name,
                f"VPN exigida para {len(vpn_required_devices)}/{len(devices)} dispositivo(s) (modo forçado).",
                "info",
            )
        else:
            append_task_log(
                task_id,
                group.name,
                f"VPN exigida para {len(vpn_required_devices)}/{len(devices)} dispositivo(s) deste lote.",
                "info",
            )

        def _execute_device_with_guards(device_obj, *, manage_vpn_flag: bool):
            """Executa 1 dispositivo com lock/circuit-breaker de Jump Host também no fluxo por grupo."""
            local_lock_info = None
            cb_jump_label = "direct"
            cb_retry_after = 0
            conn_flags = device_connection_flags.get(str(device_obj.id)) or {}
            use_jump_for_device = bool(conn_flags.get("jump"))

            if (
                getattr(device_obj, "group", None)
                and use_jump_for_device
                and getattr(device_obj.group, "jump_host", None)
            ):
                cb_jump_label = f"{device_obj.group.jump_host}:{int(getattr(device_obj.group, 'jump_port', 22) or 22)}"
                cb_open, cb_reason, cb_retry_after = _check_jump_host_circuit_breaker(cb_jump_label)
                if cb_open:
                    return False, cb_reason, "circuit_breaker", int(cb_retry_after or 0)

            try:
                with _jump_host_lock_context(str(device_obj.id), task_id=task_id, bulk_task_id=bulk_task_id) as lock_ctx:
                    local_lock_info = lock_ctx
                    success, message = backup_executor.run_backup_for_device_id(
                        str(device_obj.id),
                        manage_vpn=manage_vpn_flag,
                        task_id=task_id,
                    )
            except JumpHostSlotTimeoutError as exc:
                success, message = False, str(exc)
            except JumpHostSlotCancelledError as exc:
                success, message = False, str(exc)

            message = (message or "").strip() or "Falha sem mensagem retornada pelo executor."
            failure_category = classify_failure(message) if not success else None

            _adapt_jump_host_slots(local_lock_info, bool(success), message)
            if cb_jump_label != "direct":
                if success or failure_category == "banner_timeout":
                    _record_jump_host_outcome(cb_jump_label, True)
                elif failure_category in CB_FAILURE_CATEGORIES or failure_category == "circuit_breaker":
                    _record_jump_host_outcome(cb_jump_label, False)

            return bool(success), message, failure_category, int(cb_retry_after or 0)

        if not use_vpn_mode:
            # Fallback de segurança: grupo sem VPN, executa normal.
            append_task_log(task_id, group.name, "Grupo sem VPN, executando fluxo direto.", "warning")
            processed = 0
            cancelled = False
            for device in devices:
                _touch_bulk_activity(bulk_task_id)
                if _should_stop_now(bulk_task_id):
                    cancelled = True
                    remaining = len(devices) - processed
                    result["failed"] += max(0, remaining)
                    result["details"].append({
                        "device_id": None,
                        "device_name": "Lote",
                        "success": False,
                        "message": f"Interrompido por parada forçada com {processed}/{len(devices)} processados."
                    })
                    break
                success, message, failure_category, cb_retry_after = _execute_device_with_guards(
                    device,
                    manage_vpn_flag=False,
                )
                if cancelled:
                    break
                if success:
                    result['success'] += 1
                else:
                    result['failed'] += 1
                processed += 1
                progress = min(95, int((processed / max(1, len(devices))) * 100))
                update_task_meta(
                    task_id,
                    status="running",
                    progress=progress,
                    message=f"Processando {processed}/{len(devices)} dispositivos...",
                    completed=False,
                )
                result['details'].append({
                    'device_id': str(device.id),
                    'device_name': device.name,
                    'success': success,
                    'message': message,
                    'failure_category': failure_category,
                })
            final_status = "stopped" if cancelled else ("success" if result["failed"] == 0 else "failed")
            final_msg = (
                f"Interrompido. Sucesso: {result['success']} | Falhas: {result['failed']}"
                if cancelled
                else f"Finalizado. Sucesso: {result['success']} | Falhas: {result['failed']}"
            )
            update_task_meta(
                task_id,
                status=final_status,
                progress=100,
                message=final_msg,
                completed=True,
                result=result,
            )
            append_task_log(
                task_id,
                group.name,
                final_msg,
                "success" if final_status == "success" else "error",
            )
            return result

        append_task_log(task_id, group.name, "Conectando VPN do grupo...", "info")
        with vpn_service.vpn_session(
            group,
            logger=logger,
            timeout_seconds=VPN_GLOBAL_LOCK_TIMEOUT_SECONDS,
        ):
            append_task_log(task_id, group.name, "VPN conectada com sucesso.", "success")
            processed = 0
            cancelled = False
            bulk_fail_fast = bool(bulk_task_id) and len(devices) >= LARGE_BULK_FAIL_FAST_THRESHOLD
            if bulk_fail_fast:
                append_task_log(
                    task_id,
                    group.name,
                    (
                        f"Modo fail-fast habilitado para lote grande ({len(devices)} dispositivos): "
                        "falhas transitorias nao terao retentativas para evitar travamento do lote."
                    ),
                    "warning",
                )
            for device in devices:
                _touch_bulk_activity(bulk_task_id)
                if _should_stop_now(bulk_task_id):
                    cancelled = True
                    remaining = len(devices) - processed
                    result["failed"] += max(0, remaining)
                    result["details"].append({
                        "device_id": None,
                        "device_name": "Lote",
                        "success": False,
                        "message": f"Interrompido por parada forçada com {processed}/{len(devices)} processados."
                    })
                    append_task_log(task_id, group.name, "Parada forçada solicitada. Interrompendo dispositivos restantes.", "warning")
                    break
                update_task_meta(
                    task_id,
                    status="running",
                    progress=_multi_device_progress(len(devices), processed, 0.2),
                    message=f"Processando {processed + 1}/{len(devices)} via VPN: {device.name}",
                    completed=False,
                    total_devices=len(devices),
                    processed_devices=processed,
                    done_devices=processed,
                    success_devices=result["success"],
                    failed_devices=result["failed"],
                    current_device_name=device.name,
                    current_device_index=processed + 1,
                    current_device_fraction=0.2,
                )
                success, message, failure_category, cb_retry_after = _execute_device_with_guards(
                    device,
                    manage_vpn_flag=False,
                )
                if cancelled:
                    break
                if success:
                    result['success'] += 1
                else:
                    result['failed'] += 1
                processed += 1
                update_task_meta(
                    task_id,
                    status="running",
                    progress=_multi_device_progress(len(devices), processed, 0.0),
                    message=f"Processando {processed}/{len(devices)} dispositivos via VPN...",
                    completed=False,
                    total_devices=len(devices),
                    processed_devices=processed,
                    done_devices=processed,
                    success_devices=result["success"],
                    failed_devices=result["failed"],
                    current_device_name=device.name,
                    current_device_index=processed,
                    current_device_fraction=0.0,
                )
                result['details'].append({
                    'device_id': str(device.id),
                    'device_name': device.name,
                    'success': success,
                    'message': message,
                    'failure_category': failure_category,
                })
        append_task_log(task_id, group.name, "Desconectando VPN do grupo.", "info")
        _touch_bulk_activity(bulk_task_id)

        logger.info(
            "VPN group backup finalizado: group=%s success=%s failed=%s total=%s",
            group_id, result['success'], result['failed'], result['total']
        )
        final_status = "stopped" if cancelled else ("success" if result["failed"] == 0 else "failed")
        final_msg = (
            f"Interrompido. Sucesso: {result['success']} | Falhas: {result['failed']}"
            if cancelled
            else f"Finalizado. Sucesso: {result['success']} | Falhas: {result['failed']}"
        )
        update_task_meta(
            task_id,
            status=final_status,
            progress=100,
            message=final_msg,
            completed=True,
            result=result,
            total_devices=len(devices),
            processed_devices=processed if 'processed' in locals() else 0,
            done_devices=processed if 'processed' in locals() else 0,
            success_devices=result["success"],
            failed_devices=result["failed"],
            current_device_name=None,
            current_device_index=0,
            current_device_fraction=0.0,
        )
        append_task_log(
            task_id,
            group.name,
            final_msg,
            "success" if final_status == "success" else "error",
        )
        return result
    except VpnError as e:
        logger.error("Falha de VPN no grupo %s: %s", group_id, e)
        update_task_meta(
            task_id,
            status="failed",
            progress=100,
            message=f"Falha de VPN: {e}",
            completed=True,
            error=str(e),
        )
        append_task_log(task_id, "VPN", f"Falha de VPN: {e}", "error")
        return {'error': str(e), 'group_id': group_id}
    except Exception as e:
        logger.exception("Erro no backup VPN do grupo %s", group_id)
        update_task_meta(
            task_id,
            status="failed",
            progress=100,
            message=f"Erro no backup do grupo: {e}",
            completed=True,
            error=str(e),
        )
        append_task_log(task_id, "Sistema", f"Erro no backup do grupo: {e}", "error")
        return {'error': str(e), 'group_id': group_id}
    finally:
        try:
            db.close()
        except Exception:
            # Falha no close nao deve mascarar o erro real do backup (ex.: VPN/PPP).
            pass


@celery_app.task
def run_scheduled_backups():
    """
    Task periódica para executar backups agendados de todos os tenants.
    
    Esta task é executada pelo Celery Beat conforme agendamento.
    """
    from app.models.device import Device
    from app.models.schedule import Schedule, ScheduleFrequency
    from app.models.device_group import DeviceGroup
    from sqlalchemy.orm import joinedload
    from sqlalchemy import or_
    
    if _is_global_backup_stop_enabled():
        logger.warning("Bloqueio global de backups ativo; run_scheduled_backups nao enfileirou tarefas.")
        return {
            'schedules_checked': 0,
            'devices_queued': 0,
            'direct_devices_queued': 0,
            'vpn_groups_queued': 0,
            'initialized_next_run': 0,
            'blocked_by_force_stop': True,
        }

    if _has_active_bulk_operation():
        logger.info(
            "Lote bulk ativo detectado; run_scheduled_backups pausado para evitar concorrencia de filas."
        )
        return {
            'schedules_checked': 0,
            'devices_queued': 0,
            'direct_devices_queued': 0,
            'vpn_groups_queued': 0,
            'initialized_next_run': 0,
            'blocked_by_running_bulk': True,
        }

    db = SessionLocal()
    
    try:
        now = utc_now_naive()
        active_device_filters = [
            Device.is_active == True,
            Device.backup_scheduled == True,
            or_(Device.group_id.is_(None), DeviceGroup.is_active.is_(True)),
        ]

        schedule_rows = (
            db.query(Schedule)
            .join(Device)
            .outerjoin(DeviceGroup, Device.group_id == DeviceGroup.id)
            .options(joinedload(Schedule.device).joinedload(Device.group))
            .filter(Schedule.is_active == True, *active_device_filters)
            .all()
        )

        def _tenant_daily_time(tenant_id) -> str:
            tenant_times = [
                sanitize_daily_time(row.time)
                for row in schedule_rows
                if row.device and str(row.device.tenant_id) == str(tenant_id) and row.time
            ]
            if tenant_times:
                return Counter(tenant_times).most_common(1)[0][0]
            return "02:00"

        initialized = 0
        scheduled_device_ids = {str(row.device_id) for row in schedule_rows}
        backup_enabled_devices = (
            db.query(Device)
            .outerjoin(DeviceGroup, Device.group_id == DeviceGroup.id)
            .filter(*active_device_filters)
            .all()
        )

        for device in backup_enabled_devices:
            if str(device.id) in scheduled_device_ids:
                continue
            schedule_time = _tenant_daily_time(device.tenant_id)
            db.add(
                Schedule(
                    device_id=device.id,
                    frequency=ScheduleFrequency.DAILY,
                    time=schedule_time,
                    is_active=True,
                    next_run_at=compute_next_daily_run_at(time_str=schedule_time, reference_utc=now),
                )
            )
            initialized += 1

        if initialized:
            db.flush()
            schedule_rows = (
                db.query(Schedule)
                .join(Device)
                .outerjoin(DeviceGroup, Device.group_id == DeviceGroup.id)
                .options(joinedload(Schedule.device).joinedload(Device.group))
                .filter(Schedule.is_active == True, *active_device_filters)
                .all()
            )

        due_tenant_ids = set()
        for schedule in schedule_rows:
            schedule.frequency = ScheduleFrequency.DAILY
            schedule.day_of_week = None
            schedule.day_of_month = None
            if not schedule.time:
                schedule.time = _tenant_daily_time(schedule.device.tenant_id if schedule.device else None)
            else:
                schedule.time = sanitize_daily_time(schedule.time)
            if not schedule.next_run_at:
                schedule.next_run_at = compute_next_daily_run_at(
                    time_str=schedule.time or "02:00",
                    reference_utc=now,
                )
                initialized += 1
                continue
            if schedule.next_run_at <= now and schedule.device:
                due_tenant_ids.add(str(schedule.device.tenant_id))

        excluded_type_ids = resolve_mass_backup_excluded_type_ids(db)
        queued_devices = 0
        queued_direct = 0
        queued_jump = 0
        queued_vpn_groups = 0
        tenant_batches = 0

        for tenant_id in sorted(due_tenant_ids):
            tenant_uuid = tenant_id
            try:
                import uuid as _uuid
                tenant_uuid = _uuid.UUID(str(tenant_id))
            except Exception:
                tenant_uuid = tenant_id
            tenant_schedule_rows = [
                row for row in schedule_rows
                if row.device and str(row.device.tenant_id) == tenant_id
            ]
            tenant_time = _tenant_daily_time(tenant_id)
            tenant_devices = (
                db.query(Device)
                .options(joinedload(Device.group))
                .outerjoin(DeviceGroup, Device.group_id == DeviceGroup.id)
                .filter(Device.tenant_id == tenant_uuid, *active_device_filters)
                .all()
            )

            eligible_devices = []
            for device in tenant_devices:
                if excluded_type_ids and getattr(device, "device_type_id", None) in excluded_type_ids:
                    continue
                eligible_devices.append(device)

            due_vpn_by_group = defaultdict(lambda: {"tenant_id": tenant_id, "device_ids": []})
            due_jump_devices = []
            due_direct_devices = []

            for device in eligible_devices:
                if device.group and uses_vpn_tunnel(device.group, device=device):
                    entry = due_vpn_by_group[str(device.group_id)]
                    entry["tenant_id"] = tenant_id
                    entry["device_ids"].append(str(device.id))
                elif device.group and uses_jump_host(device.group, device=device):
                    due_jump_devices.append(str(device.id))
                else:
                    due_direct_devices.append(str(device.id))

            for device_id in due_direct_devices:
                run_backup_task.delay(device_id)
                queued_direct += 1

            for device_id in due_jump_devices:
                run_backup_task.apply_async(args=[device_id], queue="jump_queue")
                queued_jump += 1

            for group_id, data in due_vpn_by_group.items():
                unique_device_ids = sorted(set(data["device_ids"]))
                if not unique_device_ids:
                    continue
                run_vpn_group_backups_task.apply_async(
                    args=[group_id, data["tenant_id"], unique_device_ids],
                    queue="vpn_queue",
                )
                queued_vpn_groups += 1

            queued_devices += len(eligible_devices)
            tenant_batches += 1

            next_run = compute_next_daily_run_at(time_str=tenant_time, reference_utc=now + timedelta(seconds=1))
            for schedule in tenant_schedule_rows:
                schedule.frequency = ScheduleFrequency.DAILY
                schedule.time = tenant_time
                schedule.day_of_week = None
                schedule.day_of_month = None
                schedule.last_run_at = now
                schedule.next_run_at = next_run

        db.commit()

        if queued_devices > 0 or initialized > 0:
            logger.info(
                "Agendamento automatico por tenant: schedules=%s tenants_due=%s dispositivos=%s inicializados=%s",
                len(schedule_rows),
                len(due_tenant_ids),
                queued_devices,
                initialized,
            )
        return {
            'schedules_checked': len(schedule_rows),
            'tenant_batches_queued': tenant_batches,
            'devices_queued': queued_devices,
            'direct_devices_queued': queued_direct,
            'jump_devices_queued': queued_jump,
            'vpn_groups_queued': queued_vpn_groups,
            'initialized_next_run': initialized,
            'skipped_not_ready': 0,
        }
    finally:
        db.close()


@celery_app.task
def purge_expired_backups():
    """
    Remove backups expirados de acordo com a politica de retencao do plano.
    Backups de dispositivos ou grupos inativos sao preservados indefinidamente.
    """
    from app.models.backup import Backup
    from app.models.device import Device
    from app.models.tenant import Tenant
    from app.models.device_group import DeviceGroup
    from sqlalchemy import or_

    db = SessionLocal()

    try:
        tenants = db.query(Tenant).filter(Tenant.is_active == True).all()
        total_deleted = 0
        total_files_removed = 0

        for tenant in tenants:
            retention_days = settings.DEFAULT_RETENTION_DAYS
            if tenant.plan and tenant.plan.backup_retention_days:
                retention_days = tenant.plan.backup_retention_days

            cutoff = datetime.utcnow() - timedelta(days=retention_days)
            
            # Filtra apenas backups antigos de devices/grupos ATIVOS.
            # Se o device ou o grupo dele for inativo, preserva infinitamente.
            expired = db.query(Backup).join(Device).outerjoin(
                DeviceGroup, Device.group_id == DeviceGroup.id
            ).filter(
                Device.tenant_id == tenant.id,
                Backup.created_at < cutoff,
                Device.is_active == True,
                or_(Device.group_id.is_(None), DeviceGroup.is_active == True)
            ).all()

            for backup in expired:
                if backup.file_path and os.path.exists(backup.file_path):
                    try:
                        os.remove(backup.file_path)
                        total_files_removed += 1
                    except OSError:
                        logger.warning(f"Falha ao remover arquivo: {backup.file_path}")
                db.delete(backup)
                total_deleted += 1

            db.commit()

        logger.info(f"Retencao aplicada: {total_deleted} backups removidos, {total_files_removed} arquivos deletados.")
        return {'deleted': total_deleted, 'files_removed': total_files_removed}
    finally:
        db.close()


@celery_app.task
def purge_failed_backups_periodic():
    """
    Limpeza periódica de backups com status failed.
    Remove registros com mais de 3 dias para reduzir volume operacional.
    """
    from app.models.backup import Backup, BackupStatus
    from app.models.device import Device

    db = SessionLocal()
    cutoff = datetime.utcnow() - timedelta(days=3)
    storage_base = '/app/storage/backups'
    deleted = 0
    files_removed = 0

    try:
        failed_backups = (
            db.query(Backup)
            .join(Device)
            .filter(
                Backup.status == BackupStatus.FAILED,
                Backup.created_at < cutoff,
            )
            .all()
        )

        for backup in failed_backups:
            file_path = (backup.file_path or "").strip()
            absolute_path = None
            if file_path:
                absolute_path = file_path if os.path.isabs(file_path) else os.path.join(storage_base, file_path)
            if absolute_path and os.path.exists(absolute_path):
                try:
                    os.remove(absolute_path)
                    files_removed += 1
                except OSError:
                    logger.warning("Falha ao remover arquivo de backup failed (periodico): %s", absolute_path)
            db.delete(backup)
            deleted += 1

        db.commit()
        logger.info(
            "Limpeza periodica de backups failed concluida: removidos=%s arquivos=%s cutoff=%s",
            deleted,
            files_removed,
            cutoff.isoformat(),
        )
        return {"deleted": deleted, "files_removed": files_removed}
    finally:
        db.close()


@celery_app.task
def purge_activity_logs_periodic():
    """
    Limpeza periódica de logs de atividade (auditoria).
    Retenção padrão: 7 dias (configurável por ACTIVITY_LOG_RETENTION_DAYS).
    """
    db = SessionLocal()
    retention_days = max(int(getattr(settings, "ACTIVITY_LOG_RETENTION_DAYS", 7) or 7), 1)
    try:
        removed = ActivityService.prune_old_logs(db, retention_days=retention_days, dry_run=False)
        logger.info(
            "Limpeza periodica de activity logs concluida: removidos=%s retention_days=%s",
            removed,
            retention_days,
        )
        return {"removed": int(removed or 0), "retention_days": retention_days}
    finally:
        db.close()
