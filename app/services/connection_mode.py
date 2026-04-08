"""
Helpers para resolver o modo de conexao efetivo de um grupo.

Evita ambiguidades quando existem campos legados misturados
(ex.: connection_type='jump_host' mas uses_vpn=True antigo).
"""
import os


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "on", "yes", "sim"}


def _normalize_connection_type_value(value: str) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"jump", "jump_host"}:
        return "jump_host"
    if raw in {"vpn", "direct"}:
        return raw
    return ""


def _normalized_connection_type(group) -> str:
    if not group:
        return ""
    raw = getattr(group, "connection_type", None)
    return _normalize_connection_type_value(raw)


def _normalized_device_connection_override(device) -> str:
    if not device:
        return ""

    subgroup = getattr(device, "subgroup", None)
    if subgroup and bool(getattr(subgroup, "is_active", True)):
        subgroup_type = _normalize_connection_type_value(getattr(subgroup, "connection_type", None))
        if subgroup_type:
            return subgroup_type

    extra = getattr(device, "extra_parameters", None) or {}
    if not isinstance(extra, dict):
        return ""

    override = _normalize_connection_type_value(
        extra.get("connection_subgroup_type") or extra.get("subgroup_connection_type")
    )
    enabled = _truthy(
        extra.get("connection_subgroup_enabled")
        if "connection_subgroup_enabled" in extra
        else extra.get("subgroup_connection_enabled")
    )

    # Compatibilidade: se o tipo foi definido manualmente no JSON,
    # assume override ativo mesmo sem flag booleana.
    if override and (enabled or "connection_subgroup_type" in extra or "subgroup_connection_type" in extra):
        return override
    return ""


def _is_mikrotik_device(device) -> bool:
    if not device:
        return False
    try:
        type_row = getattr(device, "type", None)
        type_name = str(getattr(type_row, "name", "") or "").strip().lower()
        script_name = str(getattr(type_row, "script_name", "") or "").strip().lower()
        if (
            "mikrotik" in type_name
            or "routeros" in type_name
            or "mikrotik" in script_name
            or "routeros" in script_name
        ):
            return True
    except Exception:
        pass
    # Fallback quando relacionamento de tipo ainda nao foi carregado.
    name = str(getattr(device, "name", "") or "").strip().lower()
    return "mikrotik" in name or "routeros" in name


def _group_has_vpn_credentials(group) -> bool:
    if not group:
        return False
    vpn_server = str(getattr(group, "vpn_server", "") or "").strip()
    vpn_username = str(getattr(group, "vpn_username", "") or "").strip()
    vpn_password = getattr(group, "vpn_password_encrypted", None)
    return bool(vpn_server and vpn_username and vpn_password)


def get_effective_connection_type(group, device=None) -> str:
    override = _normalized_device_connection_override(device)
    if override:
        return override

    if not group:
        return ""

    conn_type = _normalized_connection_type(group)
    # Politica operacional: MikroTik em grupo Jump Host tende a falhar em massa.
    # Quando habilitado, re-roteia automaticamente para VPN (se o grupo possuir
    # credenciais de VPN) ou Direto (fallback), sem depender de recadastro manual.
    disable_jump_for_mikrotik = str(
        os.getenv("BACKUP_MIKROTIK_DISABLE_JUMP_HOST", "1")
    ).strip().lower() in {"1", "true", "on", "yes", "sim"}
    prefer_vpn_for_mikrotik = str(
        os.getenv("BACKUP_MIKROTIK_PREFER_VPN", "1")
    ).strip().lower() in {"1", "true", "on", "yes", "sim"}
    if (
        disable_jump_for_mikrotik
        and conn_type == "jump_host"
        and _is_mikrotik_device(device)
    ):
        if prefer_vpn_for_mikrotik and _group_has_vpn_credentials(group):
            return "vpn"
        return "direct"

    if conn_type:
        return conn_type

    # Fallback para dados legados sem connection_type consistente.
    if bool(getattr(group, "uses_jump_host", False)):
        return "jump_host"
    if bool(getattr(group, "uses_vpn", False)):
        return "vpn"
    return "direct"


def uses_jump_host(group, device=None) -> bool:
    return get_effective_connection_type(group, device=device) == "jump_host"


def uses_vpn_tunnel(group, device=None) -> bool:
    return get_effective_connection_type(group, device=device) == "vpn"
