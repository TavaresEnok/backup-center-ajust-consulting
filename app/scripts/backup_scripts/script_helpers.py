
import os
import time
import logging
from datetime import datetime
from io import StringIO

# Try to import paramiko for SSH
try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False


class BackupLogger:
    def __init__(self, device_name, task_id=None, **kwargs):
        self.device_name = device_name
        self.task_id = task_id

    def emit(self, message, level='info'):
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
        try:
            pkey = paramiko.RSAKey.from_private_key(StringIO(key))
        except:
            try:
                pkey = paramiko.Ed25519Key.from_private_key(StringIO(key))
            except:
                pass  # Will fall back to password
    
    if jump_host and jump_host.get('host'):
        # Connect via Jump Host
        jump_client = paramiko.SSHClient()
        jump_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Prepare jump key
        jump_pkey = None
        if jump_host.get('key'):
            try:
                jump_pkey = paramiko.RSAKey.from_private_key(StringIO(jump_host['key']))
            except:
                try:
                    jump_pkey = paramiko.Ed25519Key.from_private_key(StringIO(jump_host['key']))
                except:
                    pass
        
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
