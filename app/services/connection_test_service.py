import socket
import time
from dataclasses import dataclass
from typing import Optional, List, Tuple

import paramiko
import pexpect

from app.core.security import decrypt_password
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.services.backup_executor import BackupLogger
from app.services.vpn_service import VpnError, vpn_service

try:
    from netmiko import ConnectHandler
except Exception:  # pragma: no cover
    ConnectHandler = None


@dataclass
class ConnectionTestResult:
    success: bool
    message: str
    protocol: str
    elapsed_ms: int


class ConnectionTestService:
    DEFAULT_TIMEOUT = 8

    def _test_tcp_port(self, host: str, port: int, timeout: int) -> None:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()

    def _candidate_device_types(self, device: Device) -> List[str]:
        script_name = (getattr(getattr(device, "type", None), "script_name", "") or "").lower()
        type_name = (getattr(getattr(device, "type", None), "name", "") or "").lower()
        token = f"{script_name} {type_name}"

        candidates: List[str] = []

        def add(*items: str):
            for item in items:
                if item and item not in candidates:
                    candidates.append(item)

        if "tplink" in token or "tp-link" in token or "jetstream" in token:
            add("tplink_jetstream", "cisco_ios")
        if "huawei" in token:
            add("huawei", "cisco_ios")
        if "mikrotik" in token:
            add("mikrotik_routeros", "cisco_ios")
        if "cisco" in token:
            add("cisco_ios", "cisco_xe")
        if "juniper" in token:
            add("juniper_junos")
        if "ubiquiti" in token:
            add("vyos", "linux", "cisco_ios")
        if "nokia" in token:
            add("nokia_sros", "nokia_srl", "juniper_junos", "cisco_ios")
        if "arista" in token:
            add("arista_eos", "cisco_ios")
        if "a10" in token:
            add("a10", "linux", "cisco_ios")
        if "hillstone" in token:
            add("linux", "cisco_ios")
        if "olt" in token:
            add("huawei", "zte_zxros", "tplink_jetstream", "cisco_ios")

        if not candidates:
            add("cisco_ios", "huawei", "tplink_jetstream", "linux")

        if device.use_telnet:
            telnet_first: List[str] = []
            for item in candidates:
                if item.endswith("_telnet"):
                    telnet_first.append(item)
                elif item in ("cisco_ios", "huawei"):
                    telnet_first.append(f"{item}_telnet")
            candidates = list(dict.fromkeys(telnet_first + candidates))

        return candidates

    def _test_netmiko(self, device: Device, password: str, timeout: int) -> Tuple[bool, str]:
        if ConnectHandler is None:
            return False, "Netmiko indisponivel no ambiente."

        last_error = None
        candidates = self._candidate_device_types(device)
        for driver in candidates:
            try:
                with ConnectHandler(
                    device_type=driver,
                    host=device.ip_address,
                    port=int(device.port or (23 if device.use_telnet else 22)),
                    username=device.username,
                    password=password,
                    conn_timeout=max(timeout, 8),
                    banner_timeout=max(timeout, 8),
                    auth_timeout=max(timeout, 8),
                    fast_cli=False,
                ):
                    return True, driver
            except Exception as exc:
                last_error = exc

        return False, str(last_error) if last_error else "Falha de autenticacao via Netmiko."

    def _test_ssh(self, device: Device, password: str, timeout: int) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=device.ip_address,
                port=device.port or 22,
                username=device.username,
                password=password,
                timeout=timeout,
                banner_timeout=timeout,
                auth_timeout=timeout,
                look_for_keys=False,
                allow_agent=False,
            )
        finally:
            client.close()

    def _test_telnet(self, device: Device, password: str, timeout: int) -> None:
        command = f"telnet {device.ip_address} {device.port or 23}"
        session = pexpect.spawn(command, timeout=timeout, encoding="utf-8")
        try:
            prompt_login = r"[Ll]ogin[: ]|[Uu]ser(\s*[Nn]ame)?[: ]"
            prompt_pass = r"[Pp]ass(word)?[: ]"
            prompt_shell = r"[>#\]\$]\s*$|<[^>]+>"
            prompt_fail = r"[Ii]ncorrect|[Ff]ailed|[Dd]enied|[Ii]nvalid|authentication failed"

            first = session.expect([prompt_login, prompt_pass, prompt_shell, prompt_fail, pexpect.TIMEOUT, pexpect.EOF])
            if first == 0:
                session.sendline(device.username or "")
                second = session.expect([prompt_pass, prompt_shell, prompt_fail, pexpect.TIMEOUT, pexpect.EOF])
                if second == 0:
                    session.sendline(password or "")
                    final = session.expect([prompt_shell, prompt_fail, pexpect.TIMEOUT, pexpect.EOF])
                    if final != 0:
                        raise RuntimeError("Autenticacao Telnet falhou.")
                elif second != 1:
                    raise RuntimeError("Falha no fluxo de autenticacao Telnet.")
            elif first == 1:
                session.sendline(password or "")
                final = session.expect([prompt_shell, prompt_fail, pexpect.TIMEOUT, pexpect.EOF])
                if final != 0:
                    raise RuntimeError("Autenticacao Telnet falhou.")
            elif first == 2:
                return
            else:
                raise RuntimeError("Nao foi possivel completar autenticacao Telnet.")
        finally:
            if session.isalive():
                session.close(force=True)

    def test_device_connection(
        self,
        device: Device,
        group: Optional[DeviceGroup] = None,
        manage_vpn: bool = True,
        timeout: Optional[int] = None,
    ) -> ConnectionTestResult:
        timeout = int(timeout or self.DEFAULT_TIMEOUT)
        logger = BackupLogger(device.name, verbose=False)
        password = decrypt_password(device.password_encrypted)
        protocol = "telnet" if device.use_telnet else "ssh"
        started = time.monotonic()

        def _run():
            self._test_tcp_port(device.ip_address, int(device.port or (23 if device.use_telnet else 22)), timeout)

            ok, netmiko_info = self._test_netmiko(device, password, timeout)
            if ok:
                return f"Conexao validada com sucesso (netmiko: {netmiko_info})."

            fallback_error = None
            try:
                if device.use_telnet:
                    self._test_telnet(device, password, timeout)
                else:
                    self._test_ssh(device, password, timeout)
                return "Conexao validada com sucesso (fallback)."
            except Exception as exc:
                fallback_error = str(exc)

            raise RuntimeError(
                f"Falha de autenticacao. Netmiko: {netmiko_info}. Fallback: {fallback_error or 'desconhecido'}"
            )

        try:
            if group and group.uses_vpn and manage_vpn:
                with vpn_service.vpn_session(group, logger=logger):
                    msg = _run()
            else:
                msg = _run()

            elapsed = int((time.monotonic() - started) * 1000)
            return ConnectionTestResult(
                success=True,
                message=msg,
                protocol=protocol,
                elapsed_ms=elapsed,
            )
        except VpnError as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            return ConnectionTestResult(
                success=False,
                message=f"Falha ao preparar VPN: {exc}",
                protocol=protocol,
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            return ConnectionTestResult(
                success=False,
                message=str(exc) or "Falha de conexao.",
                protocol=protocol,
                elapsed_ms=elapsed,
            )


connection_test_service = ConnectionTestService()
