import re
import socket
import time
from typing import Tuple

from netmiko import ConnectHandler
import pexpect

from script_helpers import BackupLogger, prepare_backup_path


ERROR_MARKERS = (
    "invalid",
    "bad command",
    "incomplete command",
    "unknown command",
    "error:",
)

TELNET_COLLECT_TIMEOUT_SECONDS = 60
TELNET_MAX_COLLECT_DURATION_SECONDS = 1800
TELNET_MAX_PAGER_PAGES = 100000


def _looks_invalid(output: str) -> bool:
    text = (output or "").lower()
    return any(marker in text for marker in ERROR_MARKERS)


def _pick_device_types(use_telnet: bool):
    if use_telnet:
        return ["cisco_ios_telnet", "cisco_ios"]
    return ["cisco_ios", "cisco_ios_telnet"]


def _build_conn_params(device_type: str, ip: str, porta: int, usuario: str, password: str, secret: str):
    # Timeouts explicitos para evitar ficar preso em handshake/login.
    return {
        "device_type": device_type,
        "host": ip,
        "port": int(porta),
        "username": usuario,
        "password": password,
        "secret": secret,
        "conn_timeout": 25,
        "banner_timeout": 25,
        "auth_timeout": 25,
        "fast_cli": False,
    }


def _test_tcp_connect(ip: str, porta: int, timeout: int = 12) -> None:
    sock = socket.create_connection((ip, int(porta)), timeout=timeout)
    sock.close()


def _collect_config(conn):
    for cmd in ("show startup-config", "show running-config"):
        out = conn.send_command(
            cmd,
            read_timeout=240,
            strip_command=False,
            strip_prompt=False,
        )
        if out and (not _looks_invalid(out)) and len(out.strip()) > 50:
            return out, cmd
    return "", ""


def _clean_telnet_output(output: str) -> str:
    text = output or ""
    text = text.replace("\x00", "")
    # Remove "char + backspace" sequencias comuns em paginas telnet.
    while "\x08" in text:
        text = re.sub(r".\x08", "", text)
    return text


def _looks_like_valid_full_config(output: str) -> bool:
    text = (output or "").strip()
    if not text:
        return False
    if _looks_invalid(text):
        return False

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 20 and len(text) < 1200:
        return False

    evidence_tokens = (
        "system config",
        "set ",
        "gpon",
        "onu",
        "vlan",
        "service-port",
        "interface",
        "hostname",
        "linecard",
    )
    lower = text.lower()
    return any(tok in lower for tok in evidence_tokens)


def _collect_telnet_command_with_pager(
    session,
    command: str,
    pager_prompt: str,
    privileged_prompt: str,
    user_prompt: str,
    logger=None,
    timeout: int = TELNET_COLLECT_TIMEOUT_SECONDS,
    max_duration_seconds: int = TELNET_MAX_COLLECT_DURATION_SECONDS,
    max_pager_pages: int = TELNET_MAX_PAGER_PAGES,
    max_idle_seconds: int = 300,
) -> str:
    session.sendline(command)
    chunks = []
    started_at = time.monotonic()
    last_progress_at = started_at
    last_log_at = started_at
    pager_pages = 0
    total_chars = 0
    if logger:
        logger.emit(f"Iniciando coleta com '{command}'...")
    while True:
        now = time.monotonic()
        if (now - started_at) > max_duration_seconds:
            raise RuntimeError(f"Timeout ao coletar configuracao ({command}).")
        idx = session.expect([pager_prompt, privileged_prompt, user_prompt, pexpect.TIMEOUT, pexpect.EOF], timeout=timeout)
        before = session.before or ""
        if before:
            chunks.append(before)
            total_chars += len(before)
            last_progress_at = time.monotonic()
        if idx == 0:
            pager_pages += 1
            if logger and (pager_pages % 100 == 0 or (time.monotonic() - last_log_at) >= 60):
                elapsed = int(time.monotonic() - started_at)
                logger.emit(
                    f"Coleta em andamento ({command}): {pager_pages} paginas lidas, "
                    f"{total_chars} chars, {elapsed}s decorridos."
                )
                last_log_at = time.monotonic()
            if pager_pages > max_pager_pages:
                raise RuntimeError(f"Coleta interrompida: paginacao excessiva sem finalizar ({command}).")
            session.send(" ")
            continue
        if idx == 1:
            if logger:
                elapsed = int(time.monotonic() - started_at)
                logger.emit(
                    f"Coleta finalizada ({command}): {pager_pages} paginas, "
                    f"{total_chars} chars, {elapsed}s."
                )
            break
        if idx == 2:
            raise RuntimeError("Comando executado sem privilegio suficiente para coletar configuracao.")
        if idx == 3:
            if (time.monotonic() - last_progress_at) > max_idle_seconds:
                raise RuntimeError(f"Timeout ao coletar configuracao ({command}).")
            # Alguns firmwares ficam aguardando tecla de continuidade sem casar o regex do pager.
            session.send(" ")
            if logger and (time.monotonic() - last_log_at) >= 60:
                elapsed = int(time.monotonic() - started_at)
                logger.emit(
                    f"Aguardando mais dados ({command}): {pager_pages} paginas, "
                    f"{total_chars} chars, {elapsed}s."
                )
                last_log_at = time.monotonic()
            continue
        raise RuntimeError("Sessao Telnet encerrada durante coleta de configuracao.")
    return _clean_telnet_output("".join(chunks))


def _collect_telnet_with_pexpect(
    ip: str,
    porta: int,
    usuario: str,
    password: str,
    enable_password: str,
    logger=None,
) -> Tuple[str, str]:
    login_prompt = r"(?i)login[: ]|username[: ]|user[: ]"
    pass_prompt = r"(?i)password[: ]"
    user_prompt = r">\s*$"
    privileged_prompt = r"#\s*$"
    pager_prompt = r"--Press any key to continue Ctrl\+c to stop--"

    session = pexpect.spawn(f"telnet {ip} {int(porta)}", timeout=25, encoding="utf-8")
    try:
        first = session.expect([login_prompt, pass_prompt, user_prompt, privileged_prompt, pexpect.TIMEOUT, pexpect.EOF])
        if first == 0:
            session.sendline(usuario or "")
            second = session.expect([pass_prompt, user_prompt, privileged_prompt, pexpect.TIMEOUT, pexpect.EOF])
            if second == 0:
                session.sendline(password or "")
                third = session.expect([user_prompt, privileged_prompt, pass_prompt, pexpect.TIMEOUT, pexpect.EOF])
                if third == 2:
                    raise RuntimeError("Falha na autenticacao Telnet.")
                if third not in (0, 1):
                    raise RuntimeError("Nao foi possivel concluir autenticacao Telnet.")
            elif second not in (1, 2):
                raise RuntimeError("Falha no fluxo de autenticacao Telnet.")
        elif first == 1:
            session.sendline(password or "")
            second = session.expect([user_prompt, privileged_prompt, pass_prompt, pexpect.TIMEOUT, pexpect.EOF])
            if second == 2:
                raise RuntimeError("Falha na autenticacao Telnet.")
            if second not in (0, 1):
                raise RuntimeError("Nao foi possivel concluir autenticacao Telnet.")
        elif first not in (2, 3):
            raise RuntimeError("Nao foi possivel iniciar sessao Telnet.")

        # Garante modo privilegiado.
        candidate_enable_passwords = []
        for item in (enable_password, password, usuario):
            val = (item or "").strip()
            if val and val not in candidate_enable_passwords:
                candidate_enable_passwords.append(val)

        session.sendline("enable")
        enable_step = session.expect([pass_prompt, privileged_prompt, user_prompt, pexpect.TIMEOUT, pexpect.EOF])
        if enable_step == 0:
            enabled = False
            for secret in candidate_enable_passwords:
                session.sendline(secret)
                enable_done = session.expect([privileged_prompt, user_prompt, pass_prompt, pexpect.TIMEOUT, pexpect.EOF])
                if enable_done == 0:
                    enabled = True
                    break
                if enable_done == 2:
                    # O equipamento pediu senha novamente: tenta próximo candidato.
                    continue
                if enable_done in (3, 4):
                    raise RuntimeError("Sessao Telnet encerrada durante enable.")
            if not enabled:
                raise RuntimeError("Nao foi possivel entrar em modo privilegiado (enable).")
        elif enable_step == 1:
            pass
        elif enable_step == 2:
            raise RuntimeError("Nao foi possivel entrar em modo privilegiado (enable).")
        else:
            raise RuntimeError("Sessao Telnet encerrada durante enable.")

        # Padrao do guia Intelbras: evitar paginação.
        session.sendline("terminal length 0")
        session.expect([privileged_prompt, user_prompt, pexpect.TIMEOUT, pexpect.EOF], timeout=15)

        # Ordem priorizada: running-config (estado atual) e fallback startup-config.
        for cmd in ("show running-config", "show startup-config"):
            output = _collect_telnet_command_with_pager(
                session=session,
                command=cmd,
                pager_prompt=pager_prompt,
                privileged_prompt=privileged_prompt,
                user_prompt=user_prompt,
                logger=logger,
            )
            if _looks_like_valid_full_config(output):
                return output, cmd

        raise ValueError("O dispositivo não retornou uma configuração válida para backup.")
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
    logger.emit("Iniciando backup para OLT FiberHome...")

    parametros = parametros or {}
    password = parametros.get("password")
    enable_password = parametros.get("enable_password") or password or usuario
    if not password:
        msg = "Falha: 'password' é obrigatório."
        logger.emit(msg, "error")
        return False, msg, None, "CONFIGURACAO"

    use_telnet = bool(parametros.get("use_telnet") or kwargs.get("use_telnet") or kwargs.get("usar_telnet"))
    candidates = _pick_device_types(use_telnet)

    logger.emit("Etapa 1/4: Testando conexão (timeout de 25s)...")
    selected_type = None
    connection_errors = []
    if use_telnet:
        try:
            _test_tcp_connect(ip, porta or 23, timeout=12)
            selected_type = "telnet_pexpect"
        except Exception as exc:
            detail = f"tcp:{type(exc).__name__}: {exc}"
            msg = f"A conexão foi fechada, recusada ou indisponível. Detalhes: {detail}"
            logger.emit(msg, "error")
            return False, msg, None, "AUTENTICACAO"
    else:
        for device_type in candidates:
            cfg = _build_conn_params(device_type, ip, porta, usuario, password, enable_password)
            try:
                with ConnectHandler(**cfg):
                    selected_type = device_type
                    break
            except Exception as exc:
                connection_errors.append(f"{device_type}: {type(exc).__name__}: {exc}")
                continue

        if not selected_type:
            detail = "; ".join(connection_errors[:3]).strip()
            msg = "A conexão foi fechada, recusada ou as credenciais estão incorretas."
            if detail:
                msg = f"{msg} Detalhes: {detail}"
            logger.emit(msg, "error")
            return False, msg, None, "AUTENTICACAO"

    logger.emit(f"Teste de conexão bem-sucedido usando '{selected_type}'.", "success")

    caminho_local_completo = prepare_backup_path(
        backup_base_path, nome_provedor, nome_tipo_equip, nome_dispositivo, "cfg"
    )

    try:
        logger.emit("Etapa 2/4: Reconectando para realizar o backup...")
        if use_telnet:
            logger.emit("Etapa 3/4: Entrando em modo privilegiado...")
            logger.emit("Etapa 4/4: Coletando configuração...")
            output, used_cmd = _collect_telnet_with_pexpect(
                ip,
                porta,
                usuario,
                password,
                enable_password,
                logger=logger,
            )
            logger.emit(f"Coleta concluída com '{used_cmd}'.")
        else:
            cfg = _build_conn_params(selected_type, ip, porta, usuario, password, enable_password)
            with ConnectHandler(**cfg) as conn:
                logger.emit("Etapa 3/4: Entrando em modo privilegiado...")
                if not conn.check_enable_mode():
                    conn.enable()

                logger.emit("Etapa 4/4: Coletando configuração...")
                output, used_cmd = _collect_config(conn)
                if not output:
                    raise ValueError("O dispositivo não retornou configuração válida para backup.")
                logger.emit(f"Coleta concluída com '{used_cmd}'.")

        if not _looks_like_valid_full_config(output):
            raise ValueError("Saida coletada nao parece uma configuracao completa/valida.")

        with open(caminho_local_completo, "w", encoding="utf-8") as f:
            f.write(output)

        msg = f"Backup da OLT FiberHome '{nome_dispositivo}' concluído!"
        logger.emit(msg, "success")
        return True, msg, caminho_local_completo
    except Exception as e:
        error_msg = f"Falha inesperada durante o backup: {e}"
        logger.emit(error_msg, "error")
        return False, error_msg, None, "SCRIPT"
