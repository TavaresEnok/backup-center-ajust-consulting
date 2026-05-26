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


def _truthy_env(name: str, default: str = "0") -> bool:
    return str(os.getenv(name, default) or "").strip().lower() in {"1", "true", "on", "yes"}


class VpnService:
    LOCK_FILE = os.getenv("VPN_LOCK_FILE", "/app/storage/vpn_global.lock")
    SETTLE_SECONDS = int(os.getenv("VPN_SETTLE_SECONDS", "8"))
    LOCK_TIMEOUT_SECONDS = max(60, int(os.getenv("VPN_GLOBAL_LOCK_TIMEOUT_SECONDS", "3600") or 3600))
    NMCLI_BACKEND_RECHECK_SECONDS = max(10, int(os.getenv("VPN_NMCLI_BACKEND_RECHECK_SECONDS", "60") or 60))

    _nmcli_unavailable_until = 0.0
    _nmcli_unavailable_reason = ""

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

    @staticmethod
    def _is_source_connection_error(message: str) -> bool:
        normalized = str(message or "").strip().lower()
        return "could not find source connection" in normalized

    @classmethod
    def _should_retry_activation_with_eth0(cls, message: str) -> bool:
        normalized = str(message or "").strip().lower()
        if cls._is_source_connection_error(normalized):
            return True
        if "connection activation failed" not in normalized:
            return False
        return (
            "unknown reason" in normalized
            or "nm_device=eth0" in normalized
            or "device=eth0" in normalized
        )

    def _retry_connection_up_on_eth0(self, conn_name: str, logger=None):
        self._log(
            logger,
            "warning",
            (
                "Falha ao subir VPN pela origem escolhida pelo NetworkManager; "
                "reativando 'container-eth0' e tentando novamente com ifname=eth0."
            ),
        )
        self._run_nmcli(["connection", "up", "container-eth0"], check=False)
        self._run_nmcli(["connection", "up", conn_name, "ifname", "eth0"], timeout=90)

    def _group_has_vpn_credentials(self, group) -> bool:
        if not group:
            return False
        vpn_server = str(getattr(group, "vpn_server", "") or "").strip()
        vpn_username = str(getattr(group, "vpn_username", "") or "").strip()
        return bool(vpn_server and vpn_username and getattr(group, "vpn_password_encrypted", None))

    def _ensure_nmcli(self):
        if shutil.which("nmcli") is None:
            raise VpnError("nmcli não encontrado. VPN por L2TP/IPsec indisponível neste worker.")
        now = time.time()
        if now < float(self._nmcli_unavailable_until or 0.0):
            reason = self._nmcli_unavailable_reason or "backend do NetworkManager indisponível."
            raise VpnError(reason)
        try:
            result = self._run_nmcli(
                ["--terse", "--fields", "RUNNING", "general", "status"],
                timeout=10,
                check=False,
            )
            output = ((result.stdout or "") + " " + (result.stderr or "")).strip().lower()
            if result.returncode != 0 or "running" not in output:
                details = output or f"code={result.returncode}"
                reason = (
                    "NetworkManager indisponível neste worker para VPN L2TP/IPsec "
                    f"(nmcli general status: {details})."
                )
                self._nmcli_unavailable_reason = reason
                self._nmcli_unavailable_until = now + float(self.NMCLI_BACKEND_RECHECK_SECONDS)
                raise VpnError(reason)
            self._nmcli_unavailable_until = 0.0
            self._nmcli_unavailable_reason = ""
        except VpnError:
            raise
        except Exception as exc:
            reason = f"Falha ao validar backend de VPN no worker: {exc}"
            self._nmcli_unavailable_reason = reason
            self._nmcli_unavailable_until = now + float(self.NMCLI_BACKEND_RECHECK_SECONDS)
            raise VpnError(reason)

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
        if not group:
            return None
        if not bool(getattr(group, "uses_vpn", False)) and not self._group_has_vpn_credentials(group):
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
        def _emit_nm_diagnostics():
            try:
                dev_status = self._run_nmcli(["device", "status"], check=False)
                self._log(
                    logger,
                    "warning",
                    "Diagnostico NM (device status): "
                    + ((dev_status.stdout or dev_status.stderr or "").strip() or "sem saida"),
                )
            except Exception:
                pass
            try:
                active_conns = self._run_nmcli(
                    ["--terse", "--fields", "NAME,TYPE,DEVICE,STATE", "con", "show", "--active"],
                    check=False,
                )
                self._log(
                    logger,
                    "warning",
                    "Diagnostico NM (active conns): "
                    + ((active_conns.stdout or active_conns.stderr or "").strip() or "sem saida"),
                )
            except Exception:
                pass

        last_exc = None
        try:
            for attempt_vpn_data in [vpn_data]:
                self._run_nmcli(["connection", "down", conn_name], check=False)
                self._run_nmcli(["connection", "delete", conn_name], check=False)
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
                        attempt_vpn_data,
                        "vpn.secrets",
                        secrets,
                    ]
                )
                try:
                    self._run_nmcli(["connection", "up", conn_name], timeout=90)
                    last_exc = None
                    break
                except VpnError as first_exc:
                    if self._should_retry_activation_with_eth0(str(first_exc)):
                        _emit_nm_diagnostics()
                        self._retry_connection_up_on_eth0(conn_name, logger=logger)
                        last_exc = None
                        break
                    last_exc = first_exc
            if last_exc:
                raise last_exc
        except Exception:
            # Em falhas de conexão, forçamos limpeza para evitar contaminar o próximo grupo.
            _emit_nm_diagnostics()
            self._cleanup_vpn_profiles(None, logger=logger)
            self._wait_for_vpn_quiescent(timeout_seconds=20)
            raise
        self._log(logger, "success", f"VPN conectada para o grupo '{group.name}'.")
        return conn_name

    def disconnect_group_vpn(self, group, logger=None):
        if not group:
            return
        if not bool(getattr(group, "uses_vpn", False)) and not self._group_has_vpn_credentials(group):
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

    def acquire_lock(self, timeout_seconds: int | None = None):
        timeout_seconds = max(60, int(timeout_seconds or self.LOCK_TIMEOUT_SECONDS))
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
    def vpn_session(self, group, logger=None, timeout_seconds: int | None = None):
        lock_handle = None
        try:
            if not _truthy_env("VPN_ISOLATED_WORKER", "0"):
                lock_handle = self.acquire_lock(timeout_seconds=timeout_seconds)
            self.connect_group_vpn(group, logger=logger)
            yield
        finally:
            try:
                self.disconnect_group_vpn(group, logger=logger)
            finally:
                self.release_lock(lock_handle)


vpn_service = VpnService()
