from typing import Tuple, List
import re
import time

import pexpect

from script_helpers import BackupLogger, prepare_backup_path, open_pexpect_session, close_pexpect_session

PROMPT_ANY_LINE = r"(?m)^(?:<[^>\r\n]+>|\S+[>#\]])\s*$"
PAGER_RE = r"--More--|---- More ----|\[More\]|<--- More --->|Press any key|\s+More\s*\( Press 'Q' to break \)"

ERROR_MARKERS = (
    "unknown command",
    "invalid input",
    "incomplete command",
    "parameter error",
    "unrecognized",
    "error 20200",
)


def _ssh_command(ip: str, usuario: str, porta: int) -> str:
    return (
        "ssh "
        "-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null "
        "-o ConnectTimeout=25 "
        "-o HostKeyAlgorithms=+ssh-rsa,ssh-dss "
        "-o KexAlgorithms=+diffie-hellman-group1-sha1,diffie-hellman-group14-sha1 "
        "-o Ciphers=+aes128-cbc,3des-cbc "
        f"{usuario}@{ip} -p {int(porta)}"
    )


def _clean(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\x1B\[[0-9;?]*[A-Za-z]", "", text)
    text = re.sub(r".\x08", "", text)
    text = text.replace("\r", "")
    return text


def _looks_invalid(output: str) -> bool:
    low = (output or "").lower()
    return any(marker in low for marker in ERROR_MARKERS)


def _login(child, usuario: str, password: str, timeout: int = 25) -> bool:
    for _ in range(14):
        idx = child.expect(
            [
                r"(?i)are you sure you want to continue connecting",
                r"(?i)(press\s+enter|pressione\s+enter|any key to continue)",
                r"(?i)(user\s*name|username|login)[: ]",
                r"(?i)password[: ]",
                PROMPT_ANY_LINE,
                pexpect.TIMEOUT,
                pexpect.EOF,
            ],
            timeout=timeout,
        )
        if idx == 0:
            child.sendline("yes")
            continue
        if idx == 1:
            child.sendline("")
            continue
        if idx == 2:
            child.sendline(usuario)
            continue
        if idx == 3:
            child.sendline(password)
            continue
        if idx == 4:
            return True
        if idx in (5, 6):
            child.sendline("")
            continue
    return False


def _try_enable(child, secrets: List[str], timeout: int = 10) -> bool:
    child.sendline("enable")
    idx = child.expect([r"(?i)password[: ]", PROMPT_ANY_LINE, pexpect.TIMEOUT, pexpect.EOF], timeout=timeout)
    if idx == 1:
        return True
    if idx == 0:
        for secret in secrets:
            sec = (secret or "").strip()
            if not sec:
                continue
            child.sendline(sec)
            j = child.expect([PROMPT_ANY_LINE, r"(?i)password[: ]", pexpect.TIMEOUT, pexpect.EOF], timeout=timeout)
            if j == 0:
                return True
            if j == 1:
                continue
    return False


def _send_collect(child, command: str, timeout_seconds: int = 420):
    child.sendline(command)
    deadline = time.time() + timeout_seconds
    chunks = []

    while True:
        rem = int(deadline - time.time())
        if rem <= 0:
            return False, _clean("".join(chunks))

        idx = child.expect(
            [
                PROMPT_ANY_LINE,
                PAGER_RE,
                r":",
                pexpect.TIMEOUT,
                pexpect.EOF,
            ],
            timeout=max(5, min(25, rem)),
        )
        chunks.append(child.before or "")

        if idx == 0:
            return True, _clean("".join(chunks))
        if idx == 1:
            child.send(" ")
            continue
        if idx == 2:
            child.sendline("")
            continue
        if idx == 3:
            return False, _clean("".join(chunks))
        if idx == 4:
            return True, _clean("".join(chunks))


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
    logger.emit("Iniciando backup para OLT ZTE...")

    parametros = parametros or {}
    password = parametros.get("password")
    if not password:
        msg = "Falha: 'password' e um parametro obrigatorio."
        logger.emit(msg, "error")
        return (False, msg, None, "CONFIGURACAO")

    use_telnet = bool(parametros.get("use_telnet") or kwargs.get("use_telnet") or kwargs.get("usar_telnet"))
    enable_password = parametros.get("enable_password")
    secrets = [enable_password, password, usuario]
    jump_host = kwargs.get("jump_host") or parametros.get("jump_host") or None

    command = f"telnet {ip} {int(porta)}" if use_telnet else _ssh_command(ip, usuario, porta)
    backup_path = prepare_backup_path(backup_base_path, nome_provedor, nome_tipo_equip, nome_dispositivo, "cfg")

    child = None
    try:
        logger.emit(f"Etapa 1/3: Conectando {'TELNET' if use_telnet else 'SSH'}...")
        child = open_pexpect_session(
            command,
            jump_host=jump_host,
            timeout=35,
            encoding="utf-8",
            codec_errors="ignore",
            logger=logger,
        )

        if not _login(child, usuario, password, timeout=28):
            msg = "A conexao foi fechada ou as credenciais estao incorretas."
            logger.emit(msg, "error")
            return (False, msg, None, "AUTENTICACAO")

        logger.emit("Login concluido.", "success")

        logger.emit("Etapa 2/3: Preparando sessao...")
        _try_enable(child, secrets, timeout=10)

        # Melhora compatibilidade em firmwares ZTE diferentes
        for cmd in ("terminal length 0", "screen-length 0 temporary", "no page"):
            ok, out = _send_collect(child, cmd, timeout_seconds=20)
            if ok and not _looks_invalid(out):
                break

        logger.emit("Etapa 3/3: Coletando configuracao...")
        commands = [
            "show startup-config",
            "show running-config",
            "show configuration",
            "display current-configuration",
            "show config",
        ]

        best = ""
        used = None
        for cmd in commands:
            ok, out = _send_collect(child, cmd, timeout_seconds=600)
            txt = (out or "").strip()
            if not txt:
                continue

            txt = re.sub(r"^.*?" + re.escape(cmd).replace("\\ ", r"\\s+") + r"\s*", "", txt, flags=re.S).strip()

            if not _looks_invalid(txt) and len(txt) > len(best):
                best = txt
                used = cmd
            if not _looks_invalid(txt) and len(txt) >= 200:
                break

        if len(best) < 120:
            msg = "Configuracao retornada muito curta/vazia."
            logger.emit(msg, "error")
            return (False, msg, None, "SCRIPT")

        with open(backup_path, "w", encoding="utf-8") as fp:
            fp.write(best)

        msg = f"Backup da OLT ZTE '{nome_dispositivo}' concluido!"
        if used:
            msg = f"{msg} ({used})"
        logger.emit(msg, "success")
        return (True, msg, backup_path)
    except pexpect.TIMEOUT:
        msg = "Timeout durante execucao do backup na OLT ZTE."
        logger.emit(msg, "error")
        return (False, msg, None, "TIMEOUT")
    except Exception as exc:
        msg = f"Falha inesperada durante o backup: {exc}"
        logger.emit(msg, "error")
        return (False, msg, None, "SCRIPT")
    finally:
        close_pexpect_session(child)
