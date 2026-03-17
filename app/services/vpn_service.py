import fcntl
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from typing import Optional

from app.core.security import decrypt_password


class VpnError(RuntimeError):
    pass


class VpnService:
    LOCK_FILE = os.getenv("VPN_LOCK_FILE", "/app/storage/vpn_global.lock")
    SETTLE_SECONDS = int(os.getenv("VPN_SETTLE_SECONDS", "8"))

    def _log(self, logger, level: str, message: str):
        if logger and hasattr(logger, level):
            getattr(logger, level)(message)

    def _run_nmcli(self, args, timeout: int = 60, check: bool = True):
        result = subprocess.run(
            ["nmcli", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            details = stderr or stdout or f"code={result.returncode}"
            raise VpnError(f"nmcli {' '.join(args)} falhou: {details}")
        return result

    def _connection_name(self, group_id) -> str:
        key = str(group_id).replace("-", "")[:12]
        return f"group_vpn_{key}"

    def _ensure_nmcli(self):
        if shutil.which("nmcli") is None:
            raise VpnError("nmcli não encontrado. VPN por L2TP/IPsec indisponível neste worker.")

    def _cleanup_active_vpns(self, keep_connection_name: str, logger=None):
        result = self._run_nmcli(["--terse", "--fields", "NAME,TYPE,STATE", "con", "show", "--active"], check=False)
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.rsplit(":", 2)
            if len(parts) != 3:
                continue
            name, conn_type, state = parts
            if conn_type == "vpn" and state == "activated" and name != keep_connection_name:
                self._log(logger, "warning", f"Desconectando VPN ativa anterior: {name}")
                self._run_nmcli(["connection", "down", name], check=False)
                self._run_nmcli(["connection", "delete", name], check=False)

    def _cleanup_vpn_profiles(self, keep_connection_name: Optional[str] = None, logger=None):
        # Limpa VPNs ativas primeiro.
        self._cleanup_active_vpns(keep_connection_name or "", logger=logger)

        # Limpa perfis VPN residuais (mesmo inativos) para evitar estado pendente entre provedores.
        result = self._run_nmcli(["--terse", "--fields", "NAME,TYPE", "con", "show"], check=False)
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.rsplit(":", 1)
            if len(parts) != 2:
                continue
            name, conn_type = parts
            if conn_type != "vpn":
                continue
            if keep_connection_name and name == keep_connection_name:
                continue
            self._run_nmcli(["connection", "down", name], check=False)
            self._run_nmcli(["connection", "delete", name], check=False)

    def _wait_for_vpn_quiescent(self, timeout_seconds: int = 20):
        started = time.time()
        while time.time() - started < timeout_seconds:
            result = self._run_nmcli(
                ["--terse", "--fields", "NAME,TYPE,STATE", "con", "show", "--active"],
                check=False,
            )
            active_vpns = 0
            for line in (result.stdout or "").splitlines():
                parts = line.rsplit(":", 2)
                if len(parts) != 3:
                    continue
                _name, conn_type, state = parts
                if conn_type == "vpn" and state == "activated":
                    active_vpns += 1
            if active_vpns == 0:
                return
            time.sleep(1)

    def connect_group_vpn(self, group, logger=None):
        self._ensure_nmcli()
        if not group or not group.uses_vpn:
            return None

        vpn_server = (group.vpn_server or "").strip()
        vpn_user = (group.vpn_username or "").strip()
        vpn_pass = decrypt_password(group.vpn_password_encrypted) if group.vpn_password_encrypted else ""
        vpn_ipsec = decrypt_password(group.vpn_ipsec_secret_encrypted) if group.vpn_ipsec_secret_encrypted else ""

        if not vpn_server or not vpn_user or not vpn_pass:
            raise VpnError(
                f"Grupo '{group.name}' está com VPN ativa, mas faltam dados (server/user/password)."
            )

        conn_name = self._connection_name(group.id)
        self._log(logger, "info", f"Conectando VPN do grupo '{group.name}' ({conn_name})...")

        # Limpa outras VPNs e perfis residuais para evitar conflito de rotas/estado.
        self._cleanup_vpn_profiles(conn_name, logger=logger)
        self._wait_for_vpn_quiescent(timeout_seconds=20)
        time.sleep(2)

        # Remove conexão residual com o mesmo nome
        self._run_nmcli(["connection", "down", conn_name], check=False)
        self._run_nmcli(["connection", "delete", conn_name], check=False)

        vpn_data = f"gateway={vpn_server},user={vpn_user}"
        secrets = f"password-flags=0,password={vpn_pass}"
        if vpn_ipsec:
            vpn_data += ",ipsec-enabled=yes"
            secrets += f",ipsec-psk-flags=0,ipsec-psk={vpn_ipsec}"

        try:
            self._run_nmcli(
                [
                    "connection",
                    "add",
                    "type",
                    "vpn",
                    "con-name",
                    conn_name,
                    "vpn-type",
                    "l2tp",
                    "vpn.data",
                    vpn_data,
                    "vpn.secrets",
                    secrets,
                ]
            )
            self._run_nmcli(["connection", "up", conn_name], timeout=90)
        except Exception:
            # Em falhas de conexão, forçamos limpeza para evitar contaminar o próximo grupo.
            self._cleanup_vpn_profiles(None, logger=logger)
            self._wait_for_vpn_quiescent(timeout_seconds=20)
            raise
        self._log(logger, "success", f"VPN conectada para o grupo '{group.name}'.")
        return conn_name

    def disconnect_group_vpn(self, group, logger=None):
        if not group or not group.uses_vpn:
            return
        conn_name = self._connection_name(group.id)
        self._log(logger, "info", f"Desconectando VPN do grupo '{group.name}' ({conn_name})...")
        self._run_nmcli(["connection", "down", conn_name], check=False)
        self._run_nmcli(["connection", "delete", conn_name], check=False)
        self._cleanup_vpn_profiles(None, logger=logger)
        self._wait_for_vpn_quiescent(timeout_seconds=20)
        if self.SETTLE_SECONDS > 0:
            self._log(logger, "info", f"Aguardando estabilização de VPN ({self.SETTLE_SECONDS}s)...")
            time.sleep(self.SETTLE_SECONDS)

    def acquire_lock(self, timeout_seconds: int = 900):
        os.makedirs(os.path.dirname(self.LOCK_FILE), exist_ok=True)
        lock_handle = open(self.LOCK_FILE, "w", encoding="utf-8")
        start = time.time()
        while True:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                lock_handle.write(str(os.getpid()))
                lock_handle.flush()
                return lock_handle
            except BlockingIOError:
                if (time.time() - start) >= timeout_seconds:
                    lock_handle.close()
                    raise VpnError("Timeout aguardando lock global de VPN.")
                time.sleep(1)

    @staticmethod
    def release_lock(lock_handle):
        if not lock_handle:
            return
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()

    @contextmanager
    def vpn_session(self, group, logger=None, timeout_seconds: int = 900):
        lock_handle = None
        try:
            lock_handle = self.acquire_lock(timeout_seconds=timeout_seconds)
            self.connect_group_vpn(group, logger=logger)
            yield
        finally:
            try:
                self.disconnect_group_vpn(group, logger=logger)
            finally:
                self.release_lock(lock_handle)


vpn_service = VpnService()
