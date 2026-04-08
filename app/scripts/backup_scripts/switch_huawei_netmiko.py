from typing import List, Tuple

from netmiko import ConnectHandler

from script_helpers import BackupLogger, prepare_backup_path

PAGER_MARKERS = ("--More--", "---- More ----", "[More]", "<--- More --->", "Press any key")
CONFIRM_MARKERS = (
    "continue? [y/n]",
    "continue? [yes/no]",
    "are you sure",
    "[y/n]:",
    "(y/n)",
    "confirm",
)
ERROR_MARKERS = (
    "unrecognized command",
    "unknown command",
    "invalid input",
    "incomplete command",
    "command not found",
    "% error",
)

CONFIG_MARKERS = (
    "sysname ",
    "interface ",
    "vlan ",
    "aaa",
    "snmp-agent",
    "user-interface",
    "acl ",
    "return",
    "hostname ",
    "line vty",
    "end",
)

NETWORK_ERROR_MARKERS = (
    "timeout",
    "timed out",
    "no route to host",
    "connection refused",
    "tcp connection to device failed",
    "no existing session",
    "timeout opening channel",
    "channelexception",
    "eoferror",
    "connection reset by peer",
)

AUTH_ERROR_MARKERS = (
    "authentication failed",
    "auth failed",
    "permission denied",
    "invalid password",
    "login failed",
)


def _safe_exc_text(exc: Exception) -> str:
    text = str(exc).strip()
    name = getattr(getattr(exc, "__class__", None), "__name__", "Exception")
    return f"{name}: {text}" if text else name


def _classify_connection_failure(detail: str) -> str:
    lowered = (detail or "").lower()
    if any(marker in lowered for marker in NETWORK_ERROR_MARKERS):
        return "CONEXAO"
    if any(marker in lowered for marker in AUTH_ERROR_MARKERS):
        return "AUTENTICACAO"
    return "AUTENTICACAO"


def _looks_invalid(output: str) -> bool:
    lines = (output or "").splitlines()
    for line in lines[:80]:
        lowered = line.strip().lower()
        if not lowered:
            continue
        if any(marker in lowered for marker in ERROR_MARKERS):
            return True
    return False


def _looks_like_config(output: str) -> bool:
    text = (output or "").strip()
    if len(text) < 40:
        return False
    lowered = text.lower()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if "current configuration" in lowered or "configuration file" in lowered:
        return True
    if any(marker in lowered for marker in CONFIG_MARKERS):
        return True
    # Alguns firmwares retornam config "enxuta" sem cabeçalhos esperados;
    # aceita quando ha volume/estrutura suficiente e sem indício de erro.
    if len(lines) >= 6 and len(text) >= 120 and not _looks_invalid(text):
        return True
    # Configuracoes longas em Huawei podem nao conter os marcadores acima
    # em alguns modelos/firmwares; evita falso negativo.
    if len(text) >= 600 and not _looks_invalid(text):
        return True
    # Heuristica adicional para equipamentos com config curta
    if "\n#" in text or text.startswith("#"):
        return True
    return False


def _device_candidates(use_telnet: bool) -> List[str]:
    base = ["huawei", "cisco_ios", "tplink_jetstream"]
    if not use_telnet:
        return base

    telnet_first = []
    for item in base:
        if item.endswith("_telnet"):
            telnet_first.append(item)
        elif item in ("huawei", "cisco_ios"):
            telnet_first.append(f"{item}_telnet")
    return list(dict.fromkeys(telnet_first + base))


def _send_maybe_paged(conn, command: str, read_timeout: int = 300) -> str:
    output = conn.send_command_timing(
        command,
        read_timeout=read_timeout,
        strip_command=False,
        strip_prompt=False,
    )
    safety = 0
    while safety < 300:
        lowered = (output or "").lower()
        if any(marker in output for marker in PAGER_MARKERS):
            safety += 1
            output += conn.send_command_timing(
                " ",
                read_timeout=30,
                strip_command=False,
                strip_prompt=False,
            )
            continue
        if any(marker in lowered for marker in CONFIRM_MARKERS):
            safety += 1
            output += conn.send_command_timing(
                "y",
                read_timeout=30,
                strip_command=False,
                strip_prompt=False,
            )
            continue
        break
    return output


def _collect_configuration(conn) -> Tuple[str, str]:
    collected = ""
    used_cmd = None

    for cmd in (
        "display current-configuration",
        "display current-configuration all",
        "show running-config",
        "show configuration",
    ):
        for attempt in range(1, 3):
            try:
                out = _send_maybe_paged(
                    conn,
                    cmd,
                    read_timeout=360 if attempt == 1 else 600,
                )
                if out and len(out.strip()) > len(collected.strip()):
                    collected = out
                    used_cmd = cmd
                if out and not _looks_invalid(out) and _looks_like_config(out):
                    return out, cmd
            except Exception:
                continue

    return collected, used_cmd


def realizar_backup(
    ip: str,
    usuario: str,
    porta: int,
    nome_provedor: str,
    nome_tipo_equip: str,
    nome_dispositivo: str,
    parametros: dict = None,
    task_id: str = None,
    backup_base_path: str = None,
    **kwargs,
) -> Tuple:
    logger = BackupLogger(nome_dispositivo, task_id)
    logger.emit("Iniciando backup para Switch Huawei...")

    parametros = parametros or {}
    password = parametros.get("password")
    if not password:
        msg = "Falha: 'password' e um parametro obrigatorio."
        logger.emit(msg, "error")
        return (False, msg, None, "CONFIGURACAO")

    use_telnet = bool(parametros.get("use_telnet") or kwargs.get("use_telnet") or kwargs.get("usar_telnet"))
    candidates = _device_candidates(use_telnet)

    logger.emit(f"Etapa 1/4: Testando conexao {'TELNET' if use_telnet else 'SSH'}...")
    selected_type = None
    conn_errors: List[str] = []
    for device_type in candidates:
        try:
            with ConnectHandler(
                device_type=device_type,
                host=ip,
                port=int(porta),
                username=usuario,
                password=password,
                conn_timeout=25,
                banner_timeout=25,
                auth_timeout=25,
                fast_cli=False,
            ):
                selected_type = device_type
                break
        except Exception as exc:
            conn_errors.append(f"{device_type}: {_safe_exc_text(exc)}")
            continue

    if not selected_type:
        detail = "; ".join(conn_errors[:3]).strip()
        category = _classify_connection_failure(detail)
        if category == "CONEXAO":
            msg = "Falha de conectividade com o dispositivo."
        else:
            msg = "A conexao foi fechada, recusada ou as credenciais estao incorretas."
        if detail:
            msg = f"{msg} Detalhes: {detail}"
        logger.emit(msg, "error")
        return (False, msg, None, category)

    logger.emit(f"Teste de conexao bem-sucedido com '{selected_type}'.", "success")

    caminho_local = prepare_backup_path(
        backup_base_path,
        nome_provedor,
        nome_tipo_equip,
        nome_dispositivo,
        "cfg",
    )

    try:
        collected = ""
        used_cmd = None
        for conn_attempt in (1, 2):
            logger.emit(f"Etapa 2/4: Reconectando com '{selected_type}'...")
            with ConnectHandler(
                device_type=selected_type,
                host=ip,
                port=int(porta),
                username=usuario,
                password=password,
                conn_timeout=25,
                banner_timeout=25,
                auth_timeout=25,
                fast_cli=False,
            ) as net_connect:
                logger.emit("Conexao estabelecida.", "success")

                logger.emit("Etapa 3/4: Ajustando paginacao...")
                for cmd in ("screen-length 0 temporary", "terminal length 0", "screen-length disable", "scroll"):
                    try:
                        out = net_connect.send_command_timing(
                            cmd,
                            read_timeout=20,
                            strip_command=False,
                            strip_prompt=False,
                        )
                        if out and ":" in out:
                            net_connect.send_command_timing(
                                "",
                                read_timeout=15,
                                strip_command=False,
                                strip_prompt=False,
                            )
                        if not _looks_invalid(out):
                            break
                    except Exception:
                        continue

                logger.emit("Etapa 4/4: Coletando configuracao...")
                current_output, current_cmd = _collect_configuration(net_connect)
                if current_output and len(current_output.strip()) > len(collected.strip()):
                    collected = current_output
                    used_cmd = current_cmd

            if collected and not _looks_invalid(collected) and _looks_like_config(collected):
                break
            if conn_attempt < 2:
                logger.warning(
                    "Configuracao retornada nao passou na validacao (tentativa %s/2). Reconectando para nova coleta...",
                    conn_attempt,
                )

        if not collected or _looks_invalid(collected) or not _looks_like_config(collected):
            raise ValueError("O dispositivo nao retornou uma configuracao valida.")

        with open(caminho_local, "w", encoding="utf-8") as fp:
            fp.write(collected)

        msg = f"Backup do Switch Huawei '{nome_dispositivo}' concluido com sucesso ({used_cmd})."
        logger.emit(msg, "success")
        return (True, msg, caminho_local)
    except Exception as exc:
        error_msg = f"Falha inesperada durante o backup: {_safe_exc_text(exc)}"
        logger.emit(error_msg, "error")
        return (False, error_msg, None, "SCRIPT")
