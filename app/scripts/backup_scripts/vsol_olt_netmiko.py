from typing import Tuple
import re
import pexpect
from script_helpers import BackupLogger, prepare_backup_path, open_pexpect_session, close_pexpect_session

CONNECTION_MARKERS = (
    "timeout",
    "timed out",
    "connection refused",
    "unable to connect",
    "no route to host",
    "network is unreachable",
    "error reading ssh protocol banner",
    "timeout opening channel",
    "sessao com jump host encerrada",
    "sessão com jump host encerrada",
    "jump host",
    "jump_session_closed",
)

AUTH_MARKERS = (
    "authentication failed",
    "netmikoauthenticationexception",
    "senha",
    "password",
    "credenciais",
    "usuario",
    "usuário",
)


def _failure_category_from_reason(reason: str | None) -> str:
    text = str(reason or "").strip().lower()
    if not text:
        return "SCRIPT"
    if any(marker in text for marker in CONNECTION_MARKERS):
        return "CONEXAO"
    if any(marker in text for marker in AUTH_MARKERS):
        return "AUTENTICACAO"
    return "SCRIPT"


def _ssh_command(ip: str, usuario: str, porta: int) -> str:
    return (
        "ssh "
        "-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null "
        "-o ConnectTimeout=20 "
        "-o HostKeyAlgorithms=+ssh-rsa,ssh-dss "
        "-o KexAlgorithms=+diffie-hellman-group1-sha1,diffie-hellman-group14-sha1 "
        "-o Ciphers=+aes128-cbc,3des-cbc "
        f"{usuario}@{ip} -p {porta}"
    )


def _open_and_login(child, usuario: str, password: str, timeout: int = 25):
    prompt_any = r"\S+[>#]\s*$|<[^>]+>"
    for _ in range(10):
        idx = child.expect([
            r"(?i)are you sure you want to continue connecting",
            r"(?i)(user\s*name|username|login)[: ]",
            r"(?i)password[: ]",
            prompt_any,
            pexpect.TIMEOUT,
            pexpect.EOF,
        ], timeout=timeout)
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
            return child.after or ""
        raise RuntimeError("Falha no fluxo de autenticação.")
    raise RuntimeError("Não foi possível concluir autenticação.")


def _enter_enable(child, prompt_regex: str, enable_password: str, timeout: int = 20):
    child.sendline("enable")
    idx = child.expect([r"(?i)password[: ]", prompt_regex, pexpect.TIMEOUT], timeout=timeout)
    if idx == 0:
        child.sendline(enable_password)
        child.expect(prompt_regex, timeout=timeout)


def _disable_pagination(child, prompt_regex: str):
    for cmd in ("terminal length 0", "screen-length 0 temporary", "no page", "scroll"):
        child.sendline(cmd)
        idx = child.expect([prompt_regex, r":", pexpect.TIMEOUT], timeout=12)
        if idx == 0:
            return
        if idx == 1:
            child.sendline("")
            child.expect(prompt_regex, timeout=10)
            return


def _try_collect_config(child, prompt_regex: str) -> str:
    commands = ["show running-config", "show config", "display current-configuration", "show startup-config"]
    error_markers = ["unknown command", "%error", "invalid input", "incomplete command"]

    for cmd in commands:
        child.sendline(cmd)
        child.expect(prompt_regex, timeout=240)
        output = child.before or ""
        cleaned = re.sub(r"^.*" + re.escape(cmd) + r"\s*", "", output, flags=re.S).strip()
        low = cleaned.lower()
        if any(m in low for m in error_markers):
            continue
        if len(cleaned) >= 80:
            return cleaned
    return ""


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
    logger.emit("Iniciando backup para OLT EPON/VSOL...")

    parametros = parametros or {}
    password = parametros.get("password")
    enable_password = parametros.get("enable_password", password)

    if not password or not enable_password:
        msg = "Falha: 'password' e 'enable_password' são obrigatórios."
        logger.emit(msg, "error")
        return (False, msg, None, "CONFIGURACAO")

    use_telnet = bool(parametros.get("use_telnet") or kwargs.get("use_telnet") or kwargs.get("usar_telnet"))
    jump_host = kwargs.get("jump_host") or parametros.get("jump_host") or None
    prompt_regex = r"\S+[>#]\s*$|<[^>]+>"

    child_test = None
    try:
        logger.emit(f"Etapa 1/4: Testando conexão {'TELNET' if use_telnet else 'SSH'}...")
        command = f"telnet {ip} {porta}" if use_telnet else _ssh_command(ip, usuario, porta)
        child_test = open_pexpect_session(
            command,
            jump_host=jump_host,
            timeout=25,
            encoding="utf-8",
            codec_errors="ignore",
            logger=logger,
        )

        prompt = _open_and_login(child_test, usuario, password, timeout=25)
        if re.search(r">\s*$", prompt or ""):
            _enter_enable(child_test, prompt_regex, enable_password, timeout=20)
        logger.emit("Teste de conexão e 'enable' bem-sucedido.", "success")
    except Exception as exc:
        category = _failure_category_from_reason(str(exc))
        raw_tail = ""
        try:
            raw_tail = (child_test.before or "")[-200:].replace("\n", " ").replace("\r", " ")
        except Exception:
            raw_tail = ""
        if category == "CONEXAO":
            msg = (
                "Falha de conectividade com o dispositivo. "
                f"Detalhe: {type(exc).__name__}: {str(exc)[:220]}"
            )
        else:
            msg = (
                "A conexão foi fechada, recusada ou as credenciais estão incorretas. "
                f"Detalhe: {type(exc).__name__}: {str(exc)[:220]}"
            )
        if raw_tail:
            msg = f"{msg} | resposta={raw_tail}"
        logger.emit(msg, "error")
        return (False, msg, None, category)
    finally:
        close_pexpect_session(child_test)

    caminho_local = prepare_backup_path(backup_base_path, nome_provedor, nome_tipo_equip, nome_dispositivo, "cfg")
    child = None
    try:
        logger.emit("Etapa 2/4: Reconectando para realizar o backup...")
        command = f"telnet {ip} {porta}" if use_telnet else _ssh_command(ip, usuario, porta)
        child = open_pexpect_session(
            command,
            jump_host=jump_host,
            timeout=45,
            encoding="utf-8",
            codec_errors="ignore",
            logger=logger,
        )

        prompt = _open_and_login(child, usuario, password, timeout=30)
        if re.search(r">\s*$", prompt or ""):
            _enter_enable(child, prompt_regex, enable_password, timeout=20)

        logger.emit("Etapa 3/4: Desativando paginação...")
        _disable_pagination(child, prompt_regex)

        logger.emit("Etapa 4/4: Coletando e salvando configuração...")
        full_config = _try_collect_config(child, prompt_regex)

        if len(full_config) < 80:
            raise RuntimeError("A configuração retornada estava vazia/curta após limpeza.")

        with open(caminho_local, "w", encoding="utf-8") as fp:
            fp.write(full_config)

        msg = f"Backup de '{nome_dispositivo}' concluído!"
        logger.emit(msg, "success")
        return (True, msg, caminho_local)
    except Exception as exc:
        category = _failure_category_from_reason(str(exc))
        if category == "CONEXAO":
            error_msg = f"Falha de conectividade com o dispositivo. Detalhe: {exc}"
        else:
            error_msg = f"Falha inesperada durante o backup: {exc}"
        logger.emit(error_msg, "error")
        return (False, error_msg, None, category)
    finally:
        close_pexpect_session(child)
