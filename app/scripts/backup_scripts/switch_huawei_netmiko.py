from typing import List, Tuple

from netmiko import ConnectHandler

from script_helpers import BackupLogger, prepare_backup_path

PAGER_MARKERS = ("--More--", "---- More ----", "[More]", "<--- More --->", "Press any key")
ERROR_MARKERS = (
    "unrecognized command",
    "unknown command",
    "invalid input",
    "incomplete command",
    "error:",
    "%error",
)


def _looks_invalid(output: str) -> bool:
    text = (output or "").lower()
    return any(marker in text for marker in ERROR_MARKERS)


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
    while any(marker in output for marker in PAGER_MARKERS) and safety < 300:
        safety += 1
        output += conn.send_command_timing(
            " ",
            read_timeout=30,
            strip_command=False,
            strip_prompt=False,
        )
    return output


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
            conn_errors.append(f"{device_type}: {type(exc).__name__}: {exc}")
            continue

    if not selected_type:
        detail = "; ".join(conn_errors[:3]).strip()
        msg = "A conexao foi fechada, recusada ou as credenciais estao incorretas."
        if detail:
            msg = f"{msg} Detalhes: {detail}"
        logger.emit(msg, "error")
        return (False, msg, None, "AUTENTICACAO")

    logger.emit(f"Teste de conexao bem-sucedido com '{selected_type}'.", "success")

    caminho_local = prepare_backup_path(
        backup_base_path,
        nome_provedor,
        nome_tipo_equip,
        nome_dispositivo,
        "cfg",
    )

    try:
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
            collected = ""
            used_cmd = None
            for cmd in (
                "display current-configuration",
                "display current-configuration all",
                "show running-config",
                "show configuration",
            ):
                try:
                    out = _send_maybe_paged(net_connect, cmd, read_timeout=360)
                    if out and not _looks_invalid(out) and len(out.strip()) > 80:
                        collected = out
                        used_cmd = cmd
                        break
                    if out and len(out.strip()) > len(collected.strip()):
                        collected = out
                        used_cmd = cmd
                except Exception:
                    continue

            if not collected or _looks_invalid(collected) or len(collected.strip()) <= 80:
                raise ValueError("O dispositivo nao retornou uma configuracao valida.")

        with open(caminho_local, "w", encoding="utf-8") as fp:
            fp.write(collected)

        msg = f"Backup do Switch Huawei '{nome_dispositivo}' concluido com sucesso ({used_cmd})."
        logger.emit(msg, "success")
        return (True, msg, caminho_local)
    except Exception as exc:
        error_msg = f"Falha inesperada durante o backup: {exc}"
        logger.emit(error_msg, "error")
        return (False, error_msg, None, "SCRIPT")
