from typing import Tuple, List
import re
import time

import pexpect

from script_helpers import BackupLogger, prepare_backup_path

PROMPT_ANY_LINE = r"(?m)^(?:<[^>\r\n]+>|\(config\)#|\S+[>#\]])\s*$"
PROMPT_PRIV_LINE = r"(?m)^(?:\(config\)#|\S+#)\s*$"
PROMPT_CONFIG_LINE = r"(?m)^\(config\)#\s*$"
PAGER_RE = r"--More--|---- More ----|\[More\]|<--- More --->|Press any key|\s+More\s*\( Press 'Q' to break \)"

ERROR_MARKERS = (
    "unknown command",
    "invalid input",
    "incomplete command",
    "parameter error",
    "unrecognized",
    "error locates at '^'",
)


def _ssh_command(ip: str, usuario: str, porta: int) -> str:
    return (
        "ssh "
        "-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null "
        "-o ConnectTimeout=25 "
        "-o HostKeyAlgorithms=+ssh-rsa,ssh-dss "
        "-o PubkeyAcceptedAlgorithms=+ssh-rsa,ssh-dss "
        "-o KexAlgorithms=+diffie-hellman-group1-sha1,diffie-hellman-group14-sha1 "
        "-o Ciphers=+aes128-cbc,3des-cbc "
        f"{usuario}@{ip} -p {int(porta)}"
    )


def _clean_terminal_text(text: str) -> str:
    if not text:
        return ""
    # remove ANSI e corrige backspaces comuns dessas OLTs
    text = re.sub(r"\x1B\[[0-9;?]*[A-Za-z]", "", text)
    text = re.sub(r".\x08", "", text)
    text = text.replace("\r", "")
    return text


def _looks_invalid(output: str) -> bool:
    low = (output or "").lower()
    return any(marker in low for marker in ERROR_MARKERS)


def _login(child, usuario: str, password: str, timeout: int = 25) -> bool:
    user_prompt = r"(?i)(?:user\s*name|username)\s*[:>]|(?:^|\n)\s*login\s*[:>]\s*$"
    pass_prompt = r"(?i)password\s*[:>]"
    for _ in range(14):
        idx = child.expect(
            [
                r"(?i)are you sure you want to continue connecting",
                user_prompt,
                pass_prompt,
                r"(?i)(press\s+enter|pressione\s+enter|any key to continue)",
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
            child.sendline(usuario)
            continue
        if idx == 2:
            child.sendline(password)
            continue
        if idx == 3:
            child.sendline("")
            continue
        if idx == 4:
            return True
        if idx in (5, 6):
            child.sendline("")
            continue
    return False


def _try_enable(child, secrets: List[str], timeout: int = 12) -> bool:
    child.sendline("enable")
    idx = child.expect([r"(?i)password[: ]", PROMPT_PRIV_LINE, PROMPT_ANY_LINE, pexpect.TIMEOUT, pexpect.EOF], timeout=timeout)

    if idx == 1:
        return True
    if idx == 0:
        for secret in secrets:
            sec = (secret or "").strip()
            if not sec:
                continue
            child.sendline(sec)
            j = child.expect([PROMPT_PRIV_LINE, r"(?i)password[: ]", PROMPT_ANY_LINE, pexpect.TIMEOUT, pexpect.EOF], timeout=timeout)
            if j == 0:
                return True
            if j == 1:
                continue
        return False

    return False


def _send_and_collect(child, command: str, timeout_seconds: int = 420):
    child.sendline(command)
    deadline = time.time() + timeout_seconds
    chunks = []

    while True:
        remaining = int(deadline - time.time())
        if remaining <= 0:
            return False, _clean_terminal_text("".join(chunks))

        idx = child.expect(
            [
                PROMPT_ANY_LINE,
                PAGER_RE,
                r":",
                pexpect.TIMEOUT,
                pexpect.EOF,
            ],
            timeout=max(5, min(25, remaining)),
        )
        chunks.append(child.before or "")

        if idx == 0:
            return True, _clean_terminal_text("".join(chunks))
        if idx == 1:
            child.send(" ")
            continue
        if idx == 2:
            child.sendline("")
            continue
        if idx == 3:
            return False, _clean_terminal_text("".join(chunks))
        if idx == 4:
            return True, _clean_terminal_text("".join(chunks))


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
    logger.emit(f"Iniciando backup para Huawei OLT: {nome_dispositivo}...")

    parametros = parametros or {}
    password = parametros.get("password")
    if not password:
        msg = "Senha nao fornecida."
        logger.emit(msg, "error")
        return (False, msg, None, "CONFIGURACAO")

    use_telnet = bool(parametros.get("use_telnet") or kwargs.get("use_telnet") or kwargs.get("usar_telnet"))
    enable_password = parametros.get("enable_password")
    secrets = [enable_password, password, usuario]

    command = f"telnet {ip} {int(porta)}" if use_telnet else _ssh_command(ip, usuario, porta)
    backup_path = prepare_backup_path(backup_base_path, nome_provedor, nome_tipo_equip, nome_dispositivo, "cfg")

    child = None
    try:
        logger.emit("Etapa 1/3: Conectando e autenticando...")
        child = pexpect.spawn(command, timeout=35, encoding="utf-8", codec_errors="ignore")

        if not _login(child, usuario, password, timeout=28):
            msg = "A conexao foi fechada, recusada ou as credenciais estao incorretas."
            logger.emit(msg, "error")
            return (False, msg, None, "AUTENTICACAO")

        logger.emit("Login concluido.", "success")

        logger.emit("Etapa 2/3: Preparando sessao...")
        enabled = _try_enable(child, secrets, timeout=10)
        if enabled:
            logger.emit("Modo privilegiado confirmado.", "success")
        else:
            logger.emit("Enable nao confirmado; seguindo no modo atual.", "warning")

        # tenta entrar em config quando possivel (nao bloqueia se nao der)
        in_config = False
        child.sendline("config")
        idx = child.expect([PROMPT_CONFIG_LINE, PROMPT_PRIV_LINE, PROMPT_ANY_LINE, pexpect.TIMEOUT, pexpect.EOF], timeout=12)
        if idx == 0:
            in_config = True

        # desativa paginacao sem travar se comando nao existir
        for cmd in ("screen-length 0 temporary", "terminal length 0", "scroll"):
            ok, out = _send_and_collect(child, cmd, timeout_seconds=25)
            if ok and ":" not in out:
                if not _looks_invalid(out):
                    break

        logger.emit("Etapa 3/3: Coletando configuracao...")
        commands = [
            "display current-configuration",
            "display current-configuration simple",
            "display current-configuration all",
            "display saved-configuration",
            "display startup",
            "show running-config",
            "show startup-config",
        ]

        best_valid = ""
        used_valid = None
        best_any = ""
        used_any = None
        for cmd in commands:
            ok, out = _send_and_collect(child, cmd, timeout_seconds=480)
            txt = (out or "").strip()
            if not txt:
                continue

            invalid = _looks_invalid(txt)
            logger.emit(f"Diagnostico comando '{cmd}': ok={ok} len={len(txt)} invalid={invalid}")

            if len(txt) > len(best_any):
                best_any = txt
                used_any = cmd

            if not invalid and len(txt) > len(best_valid):
                best_valid = txt
                used_valid = cmd

            if not invalid and len(txt) >= 200:
                break

        selected = best_valid
        used = used_valid
        # fallback para firmwares que misturam mensagens de erro e config no mesmo bloco
        if len(selected) < 80 and len(best_any) >= 300:
            selected = best_any
            used = used_any
            logger.emit("Usando fallback do maior retorno bruto por firmware legado.", "warning")

        if len(selected) < 80:
            msg = "Falha critica durante o backup: Configuracao retornada muito curta/vazia."
            logger.emit(msg, "error")
            return (False, msg, None, "SCRIPT")

        with open(backup_path, "w", encoding="utf-8") as fp:
            fp.write(selected)

        msg = f"Backup de '{nome_dispositivo}' concluido!"
        if used:
            msg = f"{msg} ({used})"
        logger.emit(msg, "success")
        return (True, msg, backup_path)
    except pexpect.TIMEOUT:
        msg = "Timeout: O equipamento nao respondeu a tempo durante o backup."
        logger.emit(msg, "error")
        return (False, msg, None, "TIMEOUT")
    except Exception as exc:
        msg = f"Falha critica durante o backup: {exc}"
        logger.emit(msg, "error")
        return (False, msg, None, "SCRIPT")
    finally:
        if child and child.isalive():
            child.close(force=True)
