
import os
import time
import logging
import base64
import tempfile
import re
from datetime import datetime
from io import StringIO

try:
    import pexpect
    from pexpect.fdpexpect import fdspawn
    PEXPECT_AVAILABLE = True
except ImportError:
    pexpect = None
    fdspawn = None
    PEXPECT_AVAILABLE = False

# Try to import paramiko for SSH
try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False

try:
    from cryptography.hazmat.primitives import serialization
except ImportError:
    serialization = None


class BackupLogger:
    def __init__(self, device_name, task_id=None, **kwargs):
        self.device_name = device_name
        self.task_id = task_id

    @staticmethod
    def _normalize_message(message, args, kwargs):
        text = str(message)
        if args:
            try:
                text = text % args
            except Exception:
                text = " ".join([text, *[str(arg) for arg in args]])
        if kwargs:
            try:
                text = text % kwargs
            except Exception:
                pairs = " ".join(f"{k}={v}" for k, v in kwargs.items())
                text = f"{text} {pairs}".strip()
        return text

    def emit(self, message, level='info', *args, **kwargs):
        message = self._normalize_message(message, args, kwargs)
        timestamp = datetime.now().strftime('%H:%M:%S')
        logger = logging.getLogger(__name__)
        level_map = {
            'info': logging.INFO,
            'success': logging.INFO,
            'warning': logging.WARNING,
            'error': logging.ERROR,
        }
        logger.log(level_map.get(level, logging.INFO), "[%s] [%s] [%s] %s", timestamp, level.upper(), self.device_name, message)
        if self.task_id:
            try:
                from app.services.realtime_backup_logs import append_task_log
                append_task_log(str(self.task_id), self.device_name, message, level)
            except Exception:
                logger.exception("Falha ao emitir log realtime da task %s", self.task_id)

    def info(self, message, *args, **kwargs):
        self.emit(message, "info", *args, **kwargs)

    def success(self, message, *args, **kwargs):
        self.emit(message, "success", *args, **kwargs)

    def warning(self, message, *args, **kwargs):
        self.emit(message, "warning", *args, **kwargs)

    def error(self, message, *args, **kwargs):
        self.emit(message, "error", *args, **kwargs)


def sanitize_path_component(name: str) -> str:
    if not name: return "UNNAMED"
    s = "".join(c for c in name if c.isalnum() or c in (" ", "_", "-")).strip()
    return s.replace(" ", "_") or "UNNAMED"


def prepare_backup_path(base_dir, prov_name, type_name, dev_name, extension):
    if not base_dir:
        base_dir = os.path.join(os.getcwd(), 'storage', 'backups')
    
    prov_safe = sanitize_path_component(prov_name)
    type_safe = sanitize_path_component(type_name)
    dev_safe = sanitize_path_component(dev_name)
    
    final_path = os.path.join(base_dir, prov_safe, type_safe, dev_safe)
    os.makedirs(final_path, exist_ok=True)
    
    filename = f"backup_{time.strftime('%Y-%m-%d_%H-%M-%S')}.{extension}"
    return os.path.join(final_path, filename)


def _wrap_pem(header: str, body: str) -> str:
    body = "".join((body or "").split())
    chunks = [body[i : i + 64] for i in range(0, len(body), 64)]
    return f"-----BEGIN {header}-----\n" + "\n".join(chunks) + f"\n-----END {header}-----\n"


def normalize_private_key_text(raw_key: str) -> str:
    if not raw_key:
        return raw_key

    raw_key = str(raw_key).strip()
    if "BEGIN " in raw_key:
        return raw_key

    compact = "".join(raw_key.split())
    if not compact:
        return raw_key

    padding = "=" * ((4 - len(compact) % 4) % 4)
    try:
        decoded = base64.b64decode(compact + padding, validate=True)
    except Exception:
        return raw_key

    if serialization is not None:
        try:
            private_key = serialization.load_der_private_key(decoded, password=None)
            return private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode()
        except Exception:
            pass

    return _wrap_pem("PRIVATE KEY", compact)


def load_private_key(raw_key: str, logger=None):
    if not raw_key or not PARAMIKO_AVAILABLE:
        return None

    raw_key = str(raw_key).strip()
    candidates = [raw_key]
    normalized = normalize_private_key_text(raw_key)
    if normalized and normalized != raw_key:
        candidates.insert(0, normalized)
        if logger:
            logger.emit("Chave do Jump Host convertida de formato bruto/base64 para PEM.", "info")

    for candidate in candidates:
        for cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.DSSKey):
            try:
                return cls.from_private_key(StringIO(candidate))
            except Exception:
                continue
    return None


def _drain_spawn_buffer(session, max_reads: int = 8):
    if not session:
        return
    for _ in range(max_reads):
        try:
            chunk = session.read_nonblocking(size=4096, timeout=0.2)
            if not chunk:
                break
        except Exception:
            break


def _build_jump_spawn_command(jump_host: dict, timeout: int = 30, logger=None):
    if not jump_host or not jump_host.get("host") or not jump_host.get("username"):
        raise ValueError("Jump Host invalido para sessao interativa.")

    key_path = None
    key_text = normalize_private_key_text(jump_host.get("key")) if jump_host.get("key") else None
    if key_text:
        fd, key_path = tempfile.mkstemp(prefix="jump_host_", suffix=".pem")
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(key_text)
        os.chmod(key_path, 0o600)
        if logger and key_text != str(jump_host.get("key") or "").strip():
            logger.emit("Chave do Jump Host convertida de formato bruto/base64 para PEM.", "info")

    cmd = (
        "ssh -tt "
        "-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null "
        "-o LogLevel=ERROR "
        "-o ConnectionAttempts=1 "
        "-o PreferredAuthentications=publickey,password,keyboard-interactive "
        "-o PubkeyAuthentication=yes "
        "-o PasswordAuthentication=yes "
        "-o ServerAliveInterval=30 "
        "-o ServerAliveCountMax=2 "
        f"-o ConnectTimeout={int(timeout)} "
    )
    if key_path:
        cmd += f"-i {key_path} "
    cmd += f"-p {int(jump_host.get('port') or 22)} {jump_host.get('username')}@{jump_host.get('host')}"
    return cmd, key_path


def open_pexpect_session(
    command: str,
    jump_host: dict = None,
    timeout: int = 30,
    encoding: str = "utf-8",
    codec_errors: str = "ignore",
    logger=None,
):
    """
    Opens an interactive pexpect session, optionally through a Jump Host shell.
    """
    if not jump_host or not jump_host.get("host"):
        if not PEXPECT_AVAILABLE:
            raise ImportError("pexpect is not installed.")
        return pexpect.spawn(command, timeout=timeout, encoding=encoding, codec_errors=codec_errors)

    shell_retries = max(1, int(os.getenv("JUMP_HOST_SHELL_RETRIES", "7") or 7))
    shell_probe_timeout = max(4, int(os.getenv("JUMP_HOST_SHELL_PROBE_TIMEOUT_SECONDS", "14") or 14))
    last_error = None
    for attempt in range(1, shell_retries + 1):
        jump_command, key_path = _build_jump_spawn_command(jump_host, timeout=timeout, logger=logger)
        session = pexpect.spawn(jump_command, timeout=timeout, encoding=encoding, codec_errors=codec_errors)
        session.delaybeforesend = 0.05
        session._jump_key_path = key_path
        try:
            shell_prompt = r"(?m)^[^\r\n]*[#$>] ?$"
            for _ in range(16):
                idx = session.expect(
                    [
                        r"(?i)are you sure you want to continue connecting",
                        r"(?i)password\s*:",
                        r"(?i)(permission denied|too many authentication failures|kex_exchange_identification|administratively prohibited|channel open failed|no route to host|network is unreachable)",
                        shell_prompt,
                        pexpect.TIMEOUT,
                        pexpect.EOF,
                    ],
                    timeout=timeout,
                )
                if idx == 0:
                    session.sendline("yes")
                    continue
                if idx == 1:
                    jump_password = str(jump_host.get("password") or "")
                    if not jump_password:
                        raise RuntimeError("Jump Host solicitou senha, mas nenhuma senha foi configurada.")
                    session.sendline(jump_password)
                    continue
                if idx == 2:
                    detail = ""
                    try:
                        detail = ((session.before or "") + " " + (session.after or "")).strip()
                    except Exception:
                        detail = ""
                    detail = " ".join(str(detail).split())[:240]
                    if detail:
                        raise RuntimeError(f"Falha ao autenticar/estabelecer sessao no Jump Host: {detail}")
                    raise RuntimeError("Falha ao autenticar/estabelecer sessao no Jump Host.")
                if idx == 3:
                    break
                if idx == 4:
                    session.sendline("")
                    continue
                detail = ""
                try:
                    detail = ((session.before or "") + " " + (session.after or "")).strip()
                except Exception:
                    detail = ""
                detail = " ".join(str(detail).split())[:220]
                if detail:
                    raise RuntimeError(
                        "Sessao com Jump Host encerrada antes do shell ficar disponivel. "
                        f"Detalhe: {detail}"
                    )
                raise RuntimeError("Sessao com Jump Host encerrada antes do shell ficar disponivel.")
            else:
                raise RuntimeError("Nao foi possivel abrir shell interativo no Jump Host.")

            try:
                _drain_spawn_buffer(session)
            except Exception:
                pass

            # Confirma que o shell interativo realmente responde antes de iniciar o comando alvo.
            probe_token = f"__BC_JH_READY_{int(time.time() * 1000)}__"
            session.sendline(f"echo {probe_token}")
            probe_idx = session.expect(
                [
                    re.escape(probe_token),
                    shell_prompt,
                    pexpect.TIMEOUT,
                    pexpect.EOF,
                ],
                timeout=shell_probe_timeout,
            )
            if probe_idx == 3:
                raise RuntimeError(
                    "Sessao com Jump Host encerrada antes do shell ficar disponivel. "
                    "Detalhe: encerrada durante validacao do shell interativo."
                )
            if probe_idx == 2:
                session.sendline("")
                probe_idx_2 = session.expect([shell_prompt, pexpect.TIMEOUT, pexpect.EOF], timeout=shell_probe_timeout)
                if probe_idx_2 != 0:
                    raise RuntimeError(
                        "Sessao com Jump Host encerrada antes do shell ficar disponivel. "
                        "Detalhe: shell nao respondeu ao probe de prontidao."
                    )

            session.sendline(command)
            time.sleep(0.2)
            return session
        except Exception as exc:
            last_error = exc
            try:
                close_pexpect_session(session)
            except Exception:
                pass
            if logger and attempt < shell_retries:
                logger.emit(
                    f"Falha ao abrir shell no Jump Host (tentativa {attempt}/{shell_retries}). Retentando...",
                    "warning",
                )
            if attempt < shell_retries:
                backoff_seconds = min(12.0, 1.5 * (2 ** (attempt - 1)))
                time.sleep(backoff_seconds)
                continue
            raise
    if last_error:
        raise RuntimeError(str(last_error))
    raise RuntimeError("Nao foi possivel abrir shell interativo no Jump Host.")


def close_pexpect_session(session):
    if not session:
        return
    try:
        key_path = getattr(session, "_jump_key_path", None)
        if key_path and os.path.exists(key_path):
            os.remove(key_path)
    except Exception:
        pass
    try:
        if hasattr(session, "isalive") and session.isalive():
            session.close(force=True)
    except Exception:
        pass


# =============================================================================
# SSH CONNECTION HELPERS WITH JUMP HOST SUPPORT
# =============================================================================

def create_ssh_client(
    host: str,
    port: int = 22,
    username: str = None,
    password: str = None,
    key: str = None,
    jump_host: dict = None,
    timeout: int = 30
):
    """
    Creates an SSH client, optionally via a Jump Host (Bastion).
    
    Args:
        host: Target device IP/hostname
        port: Target device SSH port
        username: Target device username
        password: Target device password
        key: Target device SSH private key (PEM string)
        jump_host: Dict with jump host config: {'host', 'port', 'username', 'password', 'key'}
        timeout: Connection timeout in seconds
    
    Returns:
        paramiko.SSHClient connected to the target
    """
    if not PARAMIKO_AVAILABLE:
        raise ImportError("paramiko is not installed. Run: pip install paramiko")
    
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    # Prepare key if provided
    pkey = None
    if key:
        pkey = load_private_key(key)
    
    if jump_host and jump_host.get('host'):
        # Connect via Jump Host
        jump_client = paramiko.SSHClient()
        jump_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Prepare jump key
        jump_pkey = None
        if jump_host.get('key'):
            jump_pkey = load_private_key(jump_host['key'])
        
        # Connect to Jump Host
        jump_client.connect(
            hostname=jump_host['host'],
            port=jump_host.get('port', 22),
            username=jump_host.get('username'),
            password=jump_host.get('password'),
            pkey=jump_pkey,
            timeout=timeout,
            allow_agent=False,
            look_for_keys=False
        )
        
        # Create channel to target through Jump Host
        jump_transport = jump_client.get_transport()
        dest_addr = (host, port)
        local_addr = ('127.0.0.1', 0)
        
        channel = jump_transport.open_channel(
            'direct-tcpip',
            dest_addr,
            local_addr,
            timeout=timeout
        )
        
        # Connect to target via the channel
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            pkey=pkey,
            sock=channel,
            timeout=timeout,
            allow_agent=False,
            look_for_keys=False
        )
        
        # Store jump_client reference to prevent garbage collection
        client._jump_client = jump_client
    else:
        # Direct connection
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            pkey=pkey,
            timeout=timeout,
            allow_agent=False,
            look_for_keys=False
        )
    
    return client


def ssh_execute(client, command: str, timeout: int = 60) -> tuple:
    """
    Execute a command on SSH client.
    
    Returns:
        Tuple[str, str, int]: (stdout, stderr, exit_code)
    """
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    exit_code = stdout.channel.recv_exit_status()
    return stdout.read().decode('utf-8', errors='ignore'), stderr.read().decode('utf-8', errors='ignore'), exit_code


def close_ssh_client(client):
    """Safely close SSH client and any jump connection."""
    try:
        if hasattr(client, '_jump_client'):
            client._jump_client.close()
    except:
        pass
    try:
        client.close()
    except:
        pass
