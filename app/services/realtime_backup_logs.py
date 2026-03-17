import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis_client = None

TASK_LOG_PREFIX = "backup_center:task_logs:"
TASK_LOG_SEQ_PREFIX = "backup_center:task_logs_seq:"
TASK_META_PREFIX = "backup_center:task_meta:"
GLOBAL_LOG_KEY = "backup_center:global_logs"
GLOBAL_LOG_SEQ_KEY = "backup_center:global_logs_seq"
TTL_SECONDS = 60 * 60 * 48  # 48h


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def get_redis_client():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        _redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        _redis_client.ping()
        return _redis_client
    except Exception:
        logger.exception("Falha ao conectar Redis para realtime logs")
        _redis_client = None
        return None


def _task_log_key(task_id: str) -> str:
    return f"{TASK_LOG_PREFIX}{task_id}"


def _task_log_seq_key(task_id: str) -> str:
    return f"{TASK_LOG_SEQ_PREFIX}{task_id}"


def _task_meta_key(task_id: str) -> str:
    return f"{TASK_META_PREFIX}{task_id}"


def register_task(
    task_id: str,
    tenant_id: str,
    device_id: Optional[str] = None,
    device_name: Optional[str] = None,
    group_id: Optional[str] = None,
) -> None:
    if not task_id:
        return
    payload = {
        "task_id": str(task_id),
        "tenant_id": str(tenant_id),
        "device_id": str(device_id) if device_id else None,
        "device_name": device_name or "Dispositivo",
        "group_id": str(group_id) if group_id else None,
        "status": "queued",
        "progress": 0,
        "message": "Task enfileirada, aguardando worker...",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "completed": False,
    }
    _write_task_meta(task_id, payload)


def _write_task_meta(task_id: str, payload: Dict[str, Any]) -> None:
    client = get_redis_client()
    if not client:
        return
    key = _task_meta_key(task_id)
    try:
        client.setex(key, TTL_SECONDS, json.dumps(payload, ensure_ascii=False))
    except Exception:
        logger.exception("Falha ao salvar task meta %s", task_id)


def get_task_meta(task_id: str) -> Dict[str, Any]:
    client = get_redis_client()
    if not client or not task_id:
        return {}
    try:
        raw = client.get(_task_meta_key(task_id))
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.exception("Falha ao ler task meta %s", task_id)
        return {}


def update_task_meta(task_id: str, **fields: Any) -> Dict[str, Any]:
    if not task_id:
        return {}
    current = get_task_meta(task_id)
    current.update(fields)
    current["task_id"] = str(task_id)
    current["updated_at"] = _now_iso()
    _write_task_meta(task_id, current)
    return current


def append_task_log(
    task_id: Optional[str],
    device_name: str,
    message: str,
    level: str = "info",
) -> None:
    if not task_id:
        return

    client = get_redis_client()
    if not client:
        return

    level = (level or "info").lower().strip()
    if level not in {"info", "success", "warning", "error"}:
        level = "info"

    try:
        task_meta = get_task_meta(str(task_id))
        seq = int(client.incr(_task_log_seq_key(task_id)))
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = {
            "task_id": str(task_id),
            "seq": seq,
            "tenant_id": task_meta.get("tenant_id"),
            "device_name": device_name or "Sistema",
            "message": message,
            "level": level,
            "timestamp": timestamp,
        }
        global_seq = int(client.incr(GLOBAL_LOG_SEQ_KEY))
        entry["global_seq"] = global_seq
        serialized = json.dumps(entry, ensure_ascii=False)

        task_key = _task_log_key(task_id)
        client.rpush(task_key, serialized)
        client.ltrim(task_key, -500, -1)
        client.expire(task_key, TTL_SECONDS)
        client.expire(_task_log_seq_key(task_id), TTL_SECONDS)

        client.rpush(GLOBAL_LOG_KEY, serialized)
        client.ltrim(GLOBAL_LOG_KEY, -2000, -1)
        client.expire(GLOBAL_LOG_KEY, TTL_SECONDS)
        client.expire(GLOBAL_LOG_SEQ_KEY, TTL_SECONDS)
    except Exception:
        logger.exception("Falha ao gravar log realtime task=%s", task_id)


def get_task_logs(task_id: str, after_seq: int = 0, limit: int = 200) -> Dict[str, Any]:
    client = get_redis_client()
    if not client or not task_id:
        return {"entries": [], "last_seq": after_seq}

    try:
        raw_entries = client.lrange(_task_log_key(task_id), 0, -1) or []
    except Exception:
        logger.exception("Falha ao ler logs da task %s", task_id)
        return {"entries": [], "last_seq": after_seq}

    entries: List[Dict[str, Any]] = []
    last_seq = after_seq
    for raw in raw_entries:
        try:
            item = json.loads(raw)
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        seq = int(item.get("seq", 0) or 0)
        if seq <= after_seq:
            continue
        entries.append(item)
        if seq > last_seq:
            last_seq = seq
        if len(entries) >= limit:
            break

    return {"entries": entries, "last_seq": last_seq}


def get_global_logs(after_seq: int = 0, limit: int = 300, tenant_id: Optional[str] = None) -> Dict[str, Any]:
    client = get_redis_client()
    if not client:
        return {"entries": [], "last_seq": after_seq}
    try:
        raw_entries = client.lrange(GLOBAL_LOG_KEY, 0, -1) or []
    except Exception:
        logger.exception("Falha ao ler logs globais")
        return {"entries": [], "last_seq": after_seq}

    entries: List[Dict[str, Any]] = []
    last_seq = after_seq
    for raw in raw_entries:
        try:
            item = json.loads(raw)
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        seq = int(item.get("global_seq", 0) or 0)
        if seq <= after_seq:
            continue
        if tenant_id and str(item.get("tenant_id") or "") != str(tenant_id):
            continue
        entries.append(item)
        if seq > last_seq:
            last_seq = seq
        if len(entries) >= limit:
            break
    return {"entries": entries, "last_seq": last_seq}
