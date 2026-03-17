"""
Tasks Celery para monitoramento de dispositivos.

Essas tasks executam em background para não bloquear o servidor web.
"""

from datetime import datetime
from app.celery_app import celery_app
from app.core.database import SessionLocal
from app.models.tenant import Tenant
from app.services.monitor_service import MonitorService
import logging

logger = logging.getLogger(__name__)


def _is_global_stop_enabled() -> bool:
    try:
        from app.services.realtime_backup_logs import get_redis_client
        r = get_redis_client()
        if not r:
            return False
        return str(r.get("backup_center:force_stop_backups") or "").strip() == "1"
    except Exception:
        logger.exception("Falha ao verificar bloqueio global durante teste de conexao")
        return False


def _is_bulk_cancelled(bulk_task_id: str | None) -> bool:
    if not bulk_task_id:
        return False
    try:
        from app.services.realtime_backup_logs import get_task_meta
        meta = get_task_meta(str(bulk_task_id))
        return bool(meta.get("cancel_requested"))
    except Exception:
        logger.exception("Falha ao verificar cancelamento do lote %s", bulk_task_id)
        return False


def _should_stop_now(bulk_task_id: str | None = None) -> bool:
    return _is_global_stop_enabled() or _is_bulk_cancelled(bulk_task_id)


@celery_app.task(bind=True, max_retries=3)
def ping_device_task(self, device_id: str):
    """
    Task para verificar status de um dispositivo específico.
    
    Args:
        device_id: UUID do dispositivo a ser verificado
    """
    try:
        result = MonitorService.update_device_status(device_id)
        return {'device_id': device_id, 'status': result}
    except Exception as e:
        logger.error(f"Erro ao verificar dispositivo {device_id}: {e}")
        raise self.retry(exc=e, countdown=60)


@celery_app.task(bind=True)
def ping_tenant_devices_task(self, tenant_id: str):
    """
    Task para verificar status de todos os dispositivos de um tenant.
    
    Args:
        tenant_id: UUID do tenant
    """
    try:
        db = SessionLocal()
        result = MonitorService.check_all_tenant_devices(db, tenant_id)
        db.close()
        logger.info(f"Tenant {tenant_id}: {result['online']} online, {result['offline']} offline")
        return result
    except Exception as e:
        logger.error(f"Erro ao verificar dispositivos do tenant {tenant_id}: {e}")
        return {'error': str(e)}


@celery_app.task
def ping_all_devices_periodic():
    """
    Task periódica para verificar todos os dispositivos de todos os tenants ativos.
    
    Esta task é executada pelo Celery Beat a cada 5 minutos.
    """
    db = SessionLocal()
    
    try:
        # Busca todos os tenants ativos
        tenants = db.query(Tenant).filter(Tenant.is_active == True).all()
        
        results = {}
        for tenant in tenants:
            try:
                result = MonitorService.check_all_tenant_devices(db, str(tenant.id))
                results[str(tenant.id)] = result
                logger.info(f"Tenant {tenant.slug}: {result['online']} online, {result['offline']} offline")
            except Exception as e:
                logger.error(f"Erro no tenant {tenant.slug}: {e}")
                results[str(tenant.id)] = {'error': str(e)}
        
        return results
    finally:
        db.close()


@celery_app.task(bind=True, queue='vpn_queue')
def run_connection_test_task(self, device_id: str):
    """Executa teste de conexao/autenticacao em worker com suporte a VPN (nmcli)."""
    from app.models.device import Device
    from app.services.connection_test_service import connection_test_service

    db = SessionLocal()
    try:
        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            return {
                'ok': False,
                'error': f'Dispositivo {device_id} nao encontrado.',
                'protocol': 'unknown',
                'elapsed_ms': 0,
            }

        result = connection_test_service.test_device_connection(
            device=device,
            group=device.group,
            manage_vpn=True,
        )

        return {
            'ok': bool(result.success),
            'message': result.message,
            'protocol': result.protocol,
            'elapsed_ms': int(result.elapsed_ms),
            'device_id': device_id,
        }
    except Exception as exc:
        logger.exception('Erro ao testar conexao do dispositivo %s', device_id)
        return {
            'ok': False,
            'error': str(exc),
            'protocol': 'unknown',
            'elapsed_ms': 0,
            'device_id': device_id,
        }
    finally:
        db.close()


@celery_app.task(bind=True, queue='vpn_queue')
def run_device_connection_audit_task(self, device_id: str, bulk_task_id: str = None):
    """
    Executa auditoria de acesso de 1 dispositivo:
    1) ping
    2) login (somente se ping responder)

    Classificacoes:
    - no_ping
    - ping_ok_login_fail
    - ready
    """
    import time
    from app.models.device import Device
    from app.services.connection_test_service import connection_test_service
    from app.services.realtime_backup_logs import append_task_log, update_task_meta

    db = SessionLocal()
    task_id = str(self.request.id)
    try:
        if _should_stop_now(bulk_task_id):
            stopped = {
                "check_type": "connection_audit",
                "device_id": str(device_id),
                "ok": False,
                "ping_ok": False,
                "login_ok": False,
                "classification": "ping_ok_login_fail",
                "message": "Teste interrompido por solicitacao de parada.",
                "protocol": "unknown",
                "elapsed_ms": 0,
            }
            update_task_meta(
                task_id,
                status="stopped",
                progress=100,
                completed=True,
                message=stopped["message"],
                result=stopped,
            )
            append_task_log(task_id, "Sistema", stopped["message"], "warning")
            return stopped

        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            result = {
                "check_type": "connection_audit",
                "device_id": str(device_id),
                "ok": False,
                "ping_ok": False,
                "login_ok": False,
                "classification": "no_ping",
                "message": "Dispositivo nao encontrado.",
                "protocol": "unknown",
                "elapsed_ms": 0,
            }
            update_task_meta(
                task_id,
                status="failed",
                progress=100,
                completed=True,
                message=result["message"],
                result=result,
            )
            append_task_log(task_id, "Sistema", result["message"], "error")
            return result

        device_name = device.name or "Dispositivo"
        protocol = "telnet" if device.use_telnet else "ssh"
        update_task_meta(
            task_id,
            status="running",
            progress=15,
            completed=False,
            message="Testando conectividade de rede (ping)...",
        )
        append_task_log(task_id, device_name, "Iniciando teste de ping.", "info")

        started = time.monotonic()
        uses_vpn = bool(device.group and device.group.uses_vpn)
        vpn_ctx = None
        vpn_logger = None
        if uses_vpn:
            from app.services.backup_executor import BackupLogger
            from app.services.vpn_service import vpn_service
            vpn_logger = BackupLogger(device.name, verbose=False)
            vpn_ctx = vpn_service.vpn_session(device.group, logger=vpn_logger)
            vpn_ctx.__enter__()

        ping_ok = bool(MonitorService.ping_device(device.ip_address))
        now_iso = datetime.utcnow().isoformat() + "Z"
        extra_params = dict(device.extra_parameters or {})
        extra_params["connection_test_last_at"] = now_iso
        extra_params["connection_test_ping_ok"] = ping_ok
        extra_params["connection_test_login_ok"] = False
        extra_params["connection_test_protocol"] = protocol

        if _should_stop_now(bulk_task_id):
            stopped = {
                "check_type": "connection_audit",
                "device_id": str(device.id),
                "ok": False,
                "ping_ok": True,
                "login_ok": False,
                "classification": "ping_ok_login_fail",
                "message": "Teste interrompido por solicitacao de parada.",
                "protocol": protocol,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
            extra_params["connection_test_group"] = "ping_ok_login_fail"
            extra_params["connection_test_message"] = stopped["message"]
            device.extra_parameters = extra_params
            db.commit()
            update_task_meta(
                task_id,
                status="stopped",
                progress=100,
                completed=True,
                message=stopped["message"],
                result=stopped,
            )
            append_task_log(task_id, device_name, stopped["message"], "warning")
            return stopped

        if ping_ok:
            device.last_connection_status = "online"
            update_task_meta(
                task_id,
                status="running",
                progress=55,
                completed=False,
                message="Ping OK. Testando login/acesso...",
            )
            append_task_log(task_id, device_name, "Ping respondeu. Iniciando teste de login.", "info")
        else:
            device.last_connection_status = "offline"
            update_task_meta(
                task_id,
                status="running",
                progress=55,
                completed=False,
                message="Ping sem resposta. Validando porta/login para evitar falso negativo...",
            )
            append_task_log(
                task_id,
                device_name,
                "Ping sem resposta. Tentando validacao por porta/login (ICMP pode estar bloqueado).",
                "warning",
            )

        # Fluxo rapido para auditoria:
        # usa apenas autenticacao direta (ssh/telnet), sem varredura netmiko.
        from app.core.security import decrypt_password
        password = decrypt_password(device.password_encrypted)
        login_timeout = 6
        login_ok = False
        tcp_ok = False
        login_message = "Conexao validada com sucesso (modo rapido)."
        try:
            connection_test_service._test_tcp_port(
                device.ip_address,
                int(device.port or (23 if device.use_telnet else 22)),
                timeout=login_timeout,
            )
            tcp_ok = True
            if device.use_telnet:
                connection_test_service._test_telnet(device, password, login_timeout)
            else:
                connection_test_service._test_ssh(device, password, login_timeout)
            login_ok = True
        except Exception as exc:
            login_ok = False
            login_message = str(exc) or "Falha de autenticacao/acesso."

        connection_result = {
            "success": login_ok,
            "message": login_message,
            "protocol": protocol,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
        login_ok = bool(connection_result.get("success"))
        if login_ok:
            classification = "ready"
            device.last_connection_status = "online"
        elif ping_ok or tcp_ok:
            classification = "ping_ok_login_fail"
        else:
            classification = "no_ping"

        extra_params["connection_test_group"] = classification
        extra_params["connection_test_login_ok"] = login_ok
        extra_params["connection_test_tcp_ok"] = bool(tcp_ok)
        extra_params["connection_test_message"] = connection_result.get("message")
        extra_params["connection_test_elapsed_ms"] = int(connection_result.get("elapsed_ms") or 0)
        device.extra_parameters = extra_params
        db.commit()

        result = {
            "check_type": "connection_audit",
            "device_id": str(device.id),
            "ok": login_ok,
            "ping_ok": ping_ok,
            "login_ok": login_ok,
            "classification": classification,
            "message": connection_result.get("message"),
            "protocol": connection_result.get("protocol") or protocol,
            "elapsed_ms": int(connection_result.get("elapsed_ms") or 0),
        }
        if login_ok:
            update_task_meta(
                task_id,
                status="success",
                progress=100,
                completed=True,
                message=(
                    "Ping e login validados com sucesso."
                    if ping_ok
                    else "Ping sem resposta, mas login validado com sucesso."
                ),
                result=result,
            )
            append_task_log(
                task_id,
                device_name,
                "Ping + login OK." if ping_ok else "Ping sem resposta, mas login OK.",
                "success",
            )
        else:
            if classification == "no_ping":
                status_message = "Sem resposta ao ping e sem acesso na porta de gerencia."
                log_message = status_message
            elif ping_ok:
                status_message = f"Ping OK, login falhou: {connection_result.get('message')}"
                log_message = status_message
            else:
                status_message = f"Ping sem resposta, porta acessivel, login falhou: {connection_result.get('message')}"
                log_message = status_message
            update_task_meta(
                task_id,
                status="success",
                progress=100,
                completed=True,
                message=status_message,
                result=result,
            )
            append_task_log(
                task_id,
                device_name,
                log_message,
                "warning",
            )
        return result
    except Exception as exc:
        logger.exception("Erro ao executar auditoria de conexao do dispositivo %s", device_id)
        result = {
            "check_type": "connection_audit",
            "device_id": str(device_id),
            "ok": False,
            "ping_ok": False,
            "login_ok": False,
            "classification": "ping_ok_login_fail",
            "message": str(exc),
            "protocol": "unknown",
            "elapsed_ms": 0,
            "error": str(exc),
        }
        update_task_meta(
            task_id,
            status="failed",
            progress=100,
            completed=True,
            message=f"Erro na auditoria: {exc}",
            error=str(exc),
            result=result,
        )
        append_task_log(task_id, "Sistema", f"Erro na auditoria: {exc}", "error")
        return result
    finally:
        try:
            if 'vpn_ctx' in locals() and vpn_ctx is not None:
                vpn_ctx.__exit__(None, None, None)
        except Exception:
            pass
        db.close()
