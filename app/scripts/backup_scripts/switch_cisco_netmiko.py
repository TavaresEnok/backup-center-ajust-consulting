from typing import List, Tuple

import pexpect
from netmiko import ConnectHandler

from script_helpers import BackupLogger, prepare_backup_path


ERROR_MARKERS = (
    "invalid input",
    "unknown command",
    "unrecognized",
    "incomplete command",
    "error:",
)
PAGER_MARKERS = ("--More--", "Press any key", "---- More ----", "[More]")
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


def _invalid(output: str) -> bool:
    text = (output or "").lower()
    return any(marker in text for marker in ERROR_MARKERS)


def _classify_connection_failure(detail: str) -> str:
    lowered = (detail or "").lower()
    if any(marker in lowered for marker in NETWORK_ERROR_MARKERS):
        return "CONEXAO"
    if any(marker in lowered for marker in AUTH_ERROR_MARKERS):
        return "AUTENTICACAO"
    return "AUTENTICACAO"


def _device_candidates(use_telnet: bool) -> List[str]:
    base = ["cisco_ios", "huawei"]
    if not use_telnet:
        return base
    return ["cisco_ios_telnet", "huawei_telnet"] + base


def _send_maybe_paged(conn, command: str, read_timeout: int = 300) -> str:
    output = conn.send_command_timing(
        command,
        read_timeout=read_timeout,
        strip_command=False,
        strip_prompt=False,
    )
    safety = 0
    while any(marker in output for marker in PAGER_MARKERS) and safety < 250:
        safety += 1
        output += conn.send_command_timing(
            " ",
            read_timeout=30,
            strip_command=False,
            strip_prompt=False,
        )
    return output


def _collect_telnet_pexpect(ip: str, porta: int, usuario: str, password: str, logger: BackupLogger) -> str:
    session = pexpect.spawn(f"telnet {ip} {int(porta)}", timeout=35, encoding="utf-8", codec_errors="ignore")
    prompt = r"(?:<[^>]+>|\S+[>#])\s*$"
    try:
        for _ in range(12):
            idx = session.expect([
                r"(?i)(user\s*name|username|login)[: ]",
                r"(?i)password[: ]",
                prompt,
                r"(?i)change now|please choose",
                pexpect.TIMEOUT,
                pexpect.EOF,
            ], timeout=35)
            if idx == 0:
                session.sendline(usuario or "")
                continue
            if idx == 1:
                session.sendline(password or "")
                continue
            if idx == 2:
                break
            if idx == 3:
                # Alguns equipamentos pedem troca de senha ao conectar.
                session.sendline("N")
                continue
            if idx in (4, 5):
                session.sendline("")
                continue
        else:
            raise RuntimeError("Falha no fluxo de login Telnet.")

        for cmd in ("terminal length 0", "screen-length 0 temporary", "no page", "paginate false"):
            session.sendline(cmd)
            session.expect([prompt, r":", pexpect.TIMEOUT], timeout=12)
            if session.after == ":":
                session.sendline("")
                session.expect(prompt, timeout=12)

        best = ""
        for cmd in ("show running-config", "show configuration", "display current-configuration"):
            session.sendline(cmd)
            chunks = []
            safety = 0
            while True:
                idx = session.expect([
                    prompt,
                    r"--More--|---- More ----|\[More\]|Press any key",
                    r":",
                    pexpect.TIMEOUT,
                    pexpect.EOF,
                ], timeout=25)
                chunks.append(session.before or "")
                if idx == 0:
                    break
                if idx == 1:
                    safety += 1
                    if safety > 250:
                        break
                    session.send(" ")
                    continue
                if idx == 2:
                    session.sendline("")
                    continue
                if idx in (3, 4):
                    break
            text = "".join(chunks)
            if text and not _invalid(text) and len(text.strip()) > len(best.strip()):
                best = text
            if text and not _invalid(text) and len(text.strip()) > 80:
                break

        if not best or _invalid(best) or len(best.strip()) < 80:
            raise RuntimeError("O dispositivo nao retornou configuracao valida via Telnet fallback.")

        logger.emit("Coleta realizada via fallback Telnet (pexpect).", "warning")
        return best
    finally:
        if session.isalive():
            session.close(force=True)


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
    logger.emit(f"Iniciando backup para {nome_dispositivo} ({nome_tipo_equip})...")

    parametros = parametros or {}
    password = parametros.get("password")
    if not password:
        msg = "Falha: 'password' e um parametro obrigatorio."
        logger.emit(msg, "error")
        return (False, msg, None, "CONFIGURACAO")

    use_telnet = bool(parametros.get("use_telnet") or kwargs.get("use_telnet") or kwargs.get("usar_telnet"))
    candidates = _device_candidates(use_telnet)

    logger.emit("Etapa 1/3: Testando conexao e autenticacao...")
    selected_type = None
    errors = []
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
            errors.append(f"{device_type}: {type(exc).__name__}: {exc}")

    caminho_local = prepare_backup_path(backup_base_path, nome_provedor, nome_tipo_equip, nome_dispositivo, "cfg")

    # Fallback direto para Telnet pexpect quando Netmiko nao autentica no equipamento.
    if not selected_type and use_telnet:
        try:
            output = _collect_telnet_pexpect(ip, int(porta), usuario, password, logger)
            with open(caminho_local, "w", encoding="utf-8") as fp:
                fp.write(output)
            msg = "Backup concluido com sucesso via fallback Telnet."
            logger.emit(msg, "success")
            return (True, msg, caminho_local)
        except Exception as exc:
            errors.append(f"telnet_fallback: {type(exc).__name__}: {exc}")

    if not selected_type:
        detail = "; ".join(errors[:3])
        category = _classify_connection_failure(detail)
        if category == "CONEXAO":
            msg = "Falha de conectividade com o dispositivo."
        else:
            msg = "A conexao foi fechada, recusada ou as credenciais estao incorretas."
        if detail:
            msg = f"{msg} Detalhes: {detail}"
        logger.emit(msg, "error")
        return (False, msg, None, category)

    try:
        logger.emit(f"Etapa 2/3: Coletando configuracao com '{selected_type}'...")
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
            for cmd in ("terminal length 0", "no page", "screen-length 0 temporary", "paginate false"):
                try:
                    out = net_connect.send_command_timing(cmd, read_timeout=20)
                    if not _invalid(out):
                        break
                except Exception:
                    continue

            output = ""
            used_cmd = None
            for cmd in ("show running-config", "show configuration", "display current-configuration"):
                try:
                    out = _send_maybe_paged(net_connect, cmd, read_timeout=300)
                    if out and not _invalid(out) and len(out.strip()) > len(output.strip()):
                        output = out
                        used_cmd = cmd
                    if out and not _invalid(out) and len(out.strip()) > 80:
                        break
                except Exception:
                    continue

            if not output or _invalid(output) or len(output.strip()) < 80:
                raise RuntimeError("O dispositivo nao retornou uma configuracao valida.")

        logger.emit("Etapa 3/3: Salvando arquivo de backup...")
        with open(caminho_local, "w", encoding="utf-8") as fp:
            fp.write(output)

        msg = "Backup concluido com sucesso!"
        if used_cmd:
            msg = f"{msg} ({used_cmd})"
        logger.emit(msg, "success")
        return (True, msg, caminho_local)
    except Exception as exc:
        # Ultima tentativa em Telnet bruto se o fluxo Netmiko quebrar no meio.
        if use_telnet:
            try:
                output = _collect_telnet_pexpect(ip, int(porta), usuario, password, logger)
                with open(caminho_local, "w", encoding="utf-8") as fp:
                    fp.write(output)
                msg = "Backup concluido com sucesso via fallback Telnet."
                logger.emit(msg, "success")
                return (True, msg, caminho_local)
            except Exception:
                pass

        msg = f"Erro iteragindo com o equipamento: {exc}"
        logger.emit(msg, "error")
        return (False, msg, None, "SCRIPT")
