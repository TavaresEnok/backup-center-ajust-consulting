"""
Tasks Celery para execução de backups.

Essas tasks executam em background para não bloquear o servidor web.
"""

from app.celery_app import celery_app
from app.core.database import SessionLocal
from app.core.config import settings
import logging
import os
import calendar
from datetime import datetime, timedelta
from collections import defaultdict
import time
from celery.exceptions import Retry

logger = logging.getLogger(__name__)
MAX_READY_AGE_MINUTES = 30


def _is_global_backup_stop_enabled() -> bool:
    try:
        from app.services.realtime_backup_logs import get_redis_client
        r = get_redis_client()
        if not r:
            return False
        flag = r.get("backup_center:force_stop_backups")
        return str(flag or "").strip() == "1"
    except Exception:
        logger.exception("Falha ao verificar bloqueio global de backups")
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
    return _is_global_backup_stop_enabled() or _is_bulk_cancelled(bulk_task_id)


def _is_connection_ready_recent(device, max_age_minutes: int = MAX_READY_AGE_MINUTES) -> tuple[bool, str]:
    try:
        from app.services.backup_diagnostics import is_connection_ready_recent
        return is_connection_ready_recent(
            getattr(device, "extra_parameters", None) or {},
            max_age_minutes=max_age_minutes,
        )
    except Exception:
        logger.exception("Falha ao validar recencia de ping/login para device %s", getattr(device, "id", None))
        return False, "falha ao validar recencia do teste ping/login"


@celery_app.task(bind=True, max_retries=3)
def run_backup_task(self, device_id: str, bulk_task_id: str = None):
    """
    Task assíncrona para executar backup de um dispositivo.
    
    Args:
        device_id: UUID do dispositivo
    
    Returns:
        Dict com resultado do backup
    """
    from app.services.backup_executor import backup_executor
    from app.services.realtime_backup_logs import append_task_log, update_task_meta
    
    task_id = self.request.id
    try:
        if _should_stop_now(bulk_task_id):
            cancelled_result = {
                'device_id': device_id,
                'success': False,
                'message': 'Backup interrompido pelo operador (parada forçada).'
            }
            update_task_meta(
                task_id,
                status="stopped",
                progress=100,
                message="Execucao interrompida por parada forçada.",
                completed=True,
                result=cancelled_result,
            )
            append_task_log(task_id, "Sistema", "Task interrompida por parada forçada.", "warning")
            return cancelled_result

        logger.info(f"Iniciando backup do dispositivo {device_id}")
        update_task_meta(
            task_id,
            status="running",
            progress=10,
            message="Iniciando conexao com o dispositivo...",
            completed=False,
        )
        append_task_log(task_id, "Sistema", f"Backup iniciado para dispositivo {device_id}", "info")
        success, message = backup_executor.run_backup_for_device_id(device_id, task_id=task_id)
        from app.services.backup_diagnostics import classify_failure, is_transient_failure
        failure_category = classify_failure(message) if not success else None
        
        result = {
            'device_id': device_id,
            'success': success,
            'message': message,
            'failure_category': failure_category,
        }
        
        if success:
            logger.info(f"Backup concluído com sucesso: {device_id}")
            update_task_meta(
                task_id,
                status="success",
                progress=100,
                message=message or "Backup concluido com sucesso.",
                completed=True,
                result=result,
            )
            append_task_log(task_id, "Sistema", message or "Backup concluido com sucesso.", "success")
        else:
            if (
                not _should_stop_now(bulk_task_id)
                and failure_category
                and is_transient_failure(failure_category)
                and self.request.retries < 2
            ):
                retry_no = self.request.retries + 1
                delay = 20 * (2 ** self.request.retries)
                retry_msg = (
                    f"Falha transitória detectada ({failure_category}). "
                    f"Retentando automaticamente em {delay}s (tentativa {retry_no}/2)."
                )
                update_task_meta(
                    task_id,
                    status="retry",
                    progress=95,
                    message=retry_msg,
                    completed=False,
                    result=result,
                )
                append_task_log(task_id, "Sistema", retry_msg, "warning")
                raise self.retry(exc=RuntimeError(message or "Falha transitória"), countdown=delay)

            logger.warning(f"Backup falhou: {device_id} - {message}")
            update_task_meta(
                task_id,
                status="failed",
                progress=100,
                message=message or "Backup finalizado com falha.",
                completed=True,
                result=result,
            )
            append_task_log(task_id, "Sistema", message or "Backup finalizado com falha.", "error")
        
        return result
    except Retry:
        raise
    except Exception as e:
        logger.error(f"Erro ao executar backup {device_id}: {e}")
        update_task_meta(
            task_id,
            status="retry",
            progress=100,
            message=f"Erro na task: {e}",
            completed=False,
            error=str(e),
        )
        append_task_log(task_id, "Sistema", f"Erro na task: {e}", "error")
        raise self.retry(exc=e, countdown=60)


@celery_app.task(bind=True)
def enqueue_vpn_groups_after_direct_phase_task(
    self,
    direct_phase_results,
    tenant_id: str,
    vpn_groups_payload=None,
    bulk_task_id: str = None,
):
    """
    Callback da fase direta do backup em massa.
    Só enfileira grupos VPN após concluir os dispositivos sem VPN.
    """
    from app.services.realtime_backup_logs import (
        append_task_log,
        get_task_meta,
        register_task,
        update_task_meta,
    )

    vpn_groups_payload = vpn_groups_payload or []
    tenant_id = str(tenant_id)
    task_id = self.request.id

    if not vpn_groups_payload:
        if bulk_task_id:
            append_task_log(
                bulk_task_id,
                "Backup em massa",
                "Fase direta concluida. Nenhum grupo VPN pendente.",
                "info",
            )
        return {"queued_vpn_groups": 0, "task_ids": []}

    if _should_stop_now(bulk_task_id):
        if bulk_task_id:
            append_task_log(
                bulk_task_id,
                "Backup em massa",
                "Parada solicitada. Grupos VPN nao foram enfileirados apos a fase direta.",
                "warning",
            )
            update_task_meta(
                bulk_task_id,
                status="stopping",
                message="Parada solicitada. Fase VPN nao sera iniciada.",
            )
        return {"queued_vpn_groups": 0, "task_ids": [], "stopped": True}

    queued = 0
    new_task_ids = []
    new_task_device_count = {}

    for item in vpn_groups_payload:
        group_id = str((item or {}).get("group_id") or "").strip()
        device_ids = sorted(set((item or {}).get("device_ids") or []))
        if not group_id or not device_ids:
            continue

        vpn_args = [group_id, tenant_id, device_ids]
        if bulk_task_id:
            vpn_args.append(bulk_task_id)
        task = run_vpn_group_backups_task.apply_async(
            args=vpn_args,
            queue="vpn_queue",
        )
        queued += 1
        task_id_str = str(task.id)
        new_task_ids.append(task_id_str)
        new_task_device_count[task_id_str] = len(device_ids)

        if bulk_task_id:
            register_task(
                task_id=task_id_str,
                tenant_id=tenant_id,
                device_name=f"Grupo VPN {group_id}",
                group_id=group_id,
            )

    if bulk_task_id:
        current = get_task_meta(bulk_task_id) or {}
        current_child_ids = [str(tid) for tid in (current.get("child_task_ids") or []) if tid]
        merged_child_ids = list(dict.fromkeys(current_child_ids + new_task_ids))

        child_count = current.get("child_task_device_count") or {}
        if not isinstance(child_count, dict):
            child_count = {}
        for k, v in new_task_device_count.items():
            child_count[str(k)] = int(v)

        total_tasks = int(current.get("total_tasks") or 0)
        if total_tasks < len(merged_child_ids):
            total_tasks = len(merged_child_ids)

        update_task_meta(
            bulk_task_id,
            child_task_ids=merged_child_ids,
            child_task_device_count=child_count,
            total_tasks=total_tasks,
            status="running",
            message=(
                f"Fase direta concluida. {queued} grupo(s) VPN enfileirado(s) "
                "para a fase final."
            ),
        )
        append_task_log(
            bulk_task_id,
            "Backup em massa",
            (
                f"Fase direta concluida. Enfileirado(s) {queued} grupo(s) VPN "
                "somente apos finalizar os dispositivos sem VPN."
            ),
            "info",
        )

    append_task_log(
        task_id,
        "Sistema",
        f"Callback da fase direta finalizado. Grupos VPN enfileirados: {queued}.",
        "info",
    )
    return {"queued_vpn_groups": queued, "task_ids": new_task_ids}


@celery_app.task(bind=True)
def run_backup_group_task(self, group_id: str, tenant_id: str):
    """
    Task assíncrona para executar backup de todos os dispositivos de um grupo.
    
    Args:
        group_id: UUID do grupo
        tenant_id: UUID do tenant
    
    Returns:
        Dict com resumo dos resultados
    """
    from app.models.device import Device
    from app.models.device_group import DeviceGroup
    import uuid
    
    db = SessionLocal()
    
    try:
        logger.info(f"Iniciando backup em massa do grupo {group_id}")
        group_uuid = uuid.UUID(group_id)
        group = db.query(DeviceGroup).filter(
            DeviceGroup.id == group_uuid,
            DeviceGroup.tenant_id == tenant_id
        ).first()
        if not group:
            return {'error': f'Grupo {group_id} não encontrado para o tenant informado.'}

        devices = db.query(Device).filter(
            Device.group_id == group_uuid,
            Device.tenant_id == tenant_id,
            Device.is_active == True
        ).all()
        
        results = {
            'group_id': group_id,
            'total': len(devices),
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'details': []
        }
        
        scheduled_devices = []
        skipped_not_ready = 0
        for d in devices:
            if not d.backup_scheduled:
                continue
            ok, _ = _is_connection_ready_recent(d)
            if ok:
                scheduled_devices.append(d)
            else:
                skipped_not_ready += 1
        if group.uses_vpn and scheduled_devices:
            task = run_vpn_group_backups_task.apply_async(
                args=[group_id, tenant_id, [str(d.id) for d in scheduled_devices]],
                queue='vpn_queue'
            )
            results['details'].append({
                'group_name': group.name,
                'task_id': task.id,
                'mode': 'vpn_group'
            })
            if skipped_not_ready:
                results['details'].append({
                    'group_name': group.name,
                    'message': f'{skipped_not_ready} dispositivo(s) pulados por falta de teste ping/login recente.'
                })
            logger.info(
                "Grupo %s enfileirado na vpn_queue (%s dispositivos)",
                group_id, len(scheduled_devices)
            )
            return results

        for device in scheduled_devices:
            # Dispara task individual para cada dispositivo
            task = run_backup_task.delay(str(device.id))
            results['details'].append({
                'device_id': str(device.id),
                'device_name': device.name,
                'task_id': task.id
            })
        
        logger.info(f"Grupo {group_id}: {len(results['details'])} backups enfileirados")
        return results
    except Exception as e:
        logger.error(f"Erro no backup do grupo {group_id}: {e}")
        return {'error': str(e)}
    finally:
        db.close()


@celery_app.task(bind=True, queue='vpn_queue')
def run_vpn_group_backups_task(self, group_id: str, tenant_id: str, device_ids=None, bulk_task_id: str = None):
    """
    Executa backups de um grupo VPN em sessão única:
    conecta VPN -> executa backups -> desconecta VPN.
    """
    from app.models.device import Device
    from app.models.device_group import DeviceGroup
    from app.services.backup_executor import backup_executor
    from app.services.vpn_service import vpn_service, VpnError
    from app.services.realtime_backup_logs import append_task_log, update_task_meta
    from app.services.backup_diagnostics import classify_failure, is_transient_failure
    import uuid

    db = SessionLocal()
    device_ids = device_ids or []
    task_id = self.request.id

    try:
        group_uuid = uuid.UUID(group_id)
        group = db.query(DeviceGroup).filter(
            DeviceGroup.id == group_uuid,
            DeviceGroup.tenant_id == tenant_id
        ).first()
        if not group:
            update_task_meta(
                task_id,
                status="failed",
                progress=100,
                message=f"Grupo {group_id} nao encontrado.",
                completed=True,
                error=f"Grupo {group_id} nao encontrado.",
            )
            return {'error': f'Grupo {group_id} não encontrado.'}

        query = db.query(Device).filter(
            Device.tenant_id == tenant_id,
            Device.group_id == group_uuid,
            Device.is_active == True,
            Device.backup_scheduled == True
        )
        if device_ids:
            query = query.filter(Device.id.in_(device_ids))
        devices = query.all()

        result = {
            'group_id': group_id,
            'group_name': group.name,
            'mode': 'vpn_group',
            'total': len(devices),
            'success': 0,
            'failed': 0,
            'details': []
        }

        if not devices:
            update_task_meta(
                task_id,
                status="success",
                progress=100,
                message="Nenhum dispositivo elegivel para backup.",
                completed=True,
                result=result,
            )
            return result

        if _should_stop_now(bulk_task_id):
            result["failed"] = len(devices)
            result["details"].append({
                "device_id": None,
                "device_name": "Lote",
                "success": False,
                "message": "Interrompido por parada forçada antes do inicio da execucao."
            })
            update_task_meta(
                task_id,
                status="stopped",
                progress=100,
                message=f"Grupo {group.name} interrompido por parada forçada.",
                completed=True,
                result=result,
            )
            append_task_log(task_id, group.name, "Execucao do grupo interrompida por parada forçada.", "warning")
            return result

        update_task_meta(
            task_id,
            status="running",
            progress=5,
            message=f"Iniciando backup via VPN para grupo {group.name}...",
            completed=False,
        )
        append_task_log(task_id, group.name, "Iniciando workflow VPN do grupo.", "info")

        if not group.uses_vpn:
            # Fallback de segurança: grupo sem VPN, executa normal.
            append_task_log(task_id, group.name, "Grupo sem VPN, executando fluxo direto.", "warning")
            processed = 0
            cancelled = False
            for device in devices:
                if _should_stop_now(bulk_task_id):
                    cancelled = True
                    remaining = len(devices) - processed
                    result["failed"] += max(0, remaining)
                    result["details"].append({
                        "device_id": None,
                        "device_name": "Lote",
                        "success": False,
                        "message": f"Interrompido por parada forçada com {processed}/{len(devices)} processados."
                    })
                    break
                attempts = 0
                success = False
                message = ""
                failure_category = None
                while attempts < 3:
                    success, message = backup_executor.run_backup_for_device_id(str(device.id), task_id=task_id)
                    if success:
                        break
                    failure_category = classify_failure(message)
                    if (
                        _should_stop_now(bulk_task_id)
                        or not is_transient_failure(failure_category)
                        or attempts >= 2
                    ):
                        break
                    retry_delay = 5 * (2 ** attempts)
                    append_task_log(
                        task_id,
                        device.name,
                        (
                            f"Falha transitória ({failure_category}). "
                            f"Nova tentativa em {retry_delay}s ({attempts + 1}/2)."
                        ),
                        "warning",
                    )
                    time.sleep(retry_delay)
                    attempts += 1
                if success:
                    result['success'] += 1
                else:
                    result['failed'] += 1
                processed += 1
                progress = min(95, int((processed / max(1, len(devices))) * 100))
                update_task_meta(
                    task_id,
                    status="running",
                    progress=progress,
                    message=f"Processando {processed}/{len(devices)} dispositivos...",
                    completed=False,
                )
                result['details'].append({
                    'device_id': str(device.id),
                    'device_name': device.name,
                    'success': success,
                    'message': message,
                    'failure_category': failure_category,
                })
            final_status = "stopped" if cancelled else ("success" if result["failed"] == 0 else "failed")
            final_msg = (
                f"Interrompido. Sucesso: {result['success']} | Falhas: {result['failed']}"
                if cancelled
                else f"Finalizado. Sucesso: {result['success']} | Falhas: {result['failed']}"
            )
            update_task_meta(
                task_id,
                status=final_status,
                progress=100,
                message=final_msg,
                completed=True,
                result=result,
            )
            append_task_log(
                task_id,
                group.name,
                final_msg,
                "success" if final_status == "success" else "error",
            )
            return result

        append_task_log(task_id, group.name, "Conectando VPN do grupo...", "info")
        with vpn_service.vpn_session(group, logger=logger):
            append_task_log(task_id, group.name, "VPN conectada com sucesso.", "success")
            processed = 0
            cancelled = False
            for device in devices:
                if _should_stop_now(bulk_task_id):
                    cancelled = True
                    remaining = len(devices) - processed
                    result["failed"] += max(0, remaining)
                    result["details"].append({
                        "device_id": None,
                        "device_name": "Lote",
                        "success": False,
                        "message": f"Interrompido por parada forçada com {processed}/{len(devices)} processados."
                    })
                    append_task_log(task_id, group.name, "Parada forçada solicitada. Interrompendo dispositivos restantes.", "warning")
                    break
                attempts = 0
                success = False
                message = ""
                failure_category = None
                while attempts < 3:
                    success, message = backup_executor.run_backup_for_device_id(
                        str(device.id),
                        manage_vpn=False,
                        task_id=task_id,
                    )
                    if success:
                        break
                    failure_category = classify_failure(message)
                    if (
                        _should_stop_now(bulk_task_id)
                        or not is_transient_failure(failure_category)
                        or attempts >= 2
                    ):
                        break
                    retry_delay = 5 * (2 ** attempts)
                    append_task_log(
                        task_id,
                        device.name,
                        (
                            f"Falha transitória ({failure_category}). "
                            f"Nova tentativa em {retry_delay}s ({attempts + 1}/2)."
                        ),
                        "warning",
                    )
                    time.sleep(retry_delay)
                    attempts += 1
                if success:
                    result['success'] += 1
                else:
                    result['failed'] += 1
                processed += 1
                progress = min(95, int((processed / max(1, len(devices))) * 100))
                update_task_meta(
                    task_id,
                    status="running",
                    progress=progress,
                    message=f"Processando {processed}/{len(devices)} dispositivos via VPN...",
                    completed=False,
                )
                result['details'].append({
                    'device_id': str(device.id),
                    'device_name': device.name,
                    'success': success,
                    'message': message,
                    'failure_category': failure_category,
                })
        append_task_log(task_id, group.name, "Desconectando VPN do grupo.", "info")

        logger.info(
            "VPN group backup finalizado: group=%s success=%s failed=%s total=%s",
            group_id, result['success'], result['failed'], result['total']
        )
        final_status = "stopped" if cancelled else ("success" if result["failed"] == 0 else "failed")
        final_msg = (
            f"Interrompido. Sucesso: {result['success']} | Falhas: {result['failed']}"
            if cancelled
            else f"Finalizado. Sucesso: {result['success']} | Falhas: {result['failed']}"
        )
        update_task_meta(
            task_id,
            status=final_status,
            progress=100,
            message=final_msg,
            completed=True,
            result=result,
        )
        append_task_log(
            task_id,
            group.name,
            final_msg,
            "success" if final_status == "success" else "error",
        )
        return result
    except VpnError as e:
        logger.error("Falha de VPN no grupo %s: %s", group_id, e)
        update_task_meta(
            task_id,
            status="failed",
            progress=100,
            message=f"Falha de VPN: {e}",
            completed=True,
            error=str(e),
        )
        append_task_log(task_id, "VPN", f"Falha de VPN: {e}", "error")
        return {'error': str(e), 'group_id': group_id}
    except Exception as e:
        logger.exception("Erro no backup VPN do grupo %s", group_id)
        update_task_meta(
            task_id,
            status="failed",
            progress=100,
            message=f"Erro no backup do grupo: {e}",
            completed=True,
            error=str(e),
        )
        append_task_log(task_id, "Sistema", f"Erro no backup do grupo: {e}", "error")
        return {'error': str(e), 'group_id': group_id}
    finally:
        db.close()


@celery_app.task
def run_scheduled_backups():
    """
    Task periódica para executar backups agendados de todos os tenants.
    
    Esta task é executada pelo Celery Beat conforme agendamento.
    """
    from app.models.device import Device
    from app.models.schedule import Schedule
    from sqlalchemy.orm import joinedload
    
    if _is_global_backup_stop_enabled():
        logger.warning("Bloqueio global de backups ativo; run_scheduled_backups nao enfileirou tarefas.")
        return {
            'schedules_checked': 0,
            'devices_queued': 0,
            'direct_devices_queued': 0,
            'vpn_groups_queued': 0,
            'initialized_next_run': 0,
            'blocked_by_force_stop': True,
        }

    db = SessionLocal()
    
    try:
        now = datetime.utcnow()
        schedules = db.query(Schedule).join(Device).options(
            joinedload(Schedule.device).joinedload(Device.group)
        ).filter(
            Schedule.is_active == True,
            Device.is_active == True,
            Device.backup_scheduled == True
        ).all()

        def _frequency_value(schedule):
            value = schedule.frequency
            return value.value if hasattr(value, "value") else str(value)

        def _next_run(schedule, reference):
            hh, mm = map(int, (schedule.time or "00:00").split(":"))
            frequency = _frequency_value(schedule)

            if frequency == "weekly":
                target_weekday = schedule.day_of_week if schedule.day_of_week is not None else reference.weekday()
                target_weekday = max(0, min(int(target_weekday), 6))
                candidate = reference.replace(hour=hh, minute=mm, second=0, microsecond=0)
                days_ahead = (target_weekday - candidate.weekday()) % 7
                candidate += timedelta(days=days_ahead)
                if candidate <= reference:
                    candidate += timedelta(days=7)
                return candidate

            if frequency == "monthly":
                target_day = schedule.day_of_month or 1
                target_day = max(1, min(int(target_day), 31))
                year, month = reference.year, reference.month
                last_day = calendar.monthrange(year, month)[1]
                candidate = datetime(year, month, min(target_day, last_day), hh, mm)
                if candidate <= reference:
                    month += 1
                    if month > 12:
                        month = 1
                        year += 1
                    last_day = calendar.monthrange(year, month)[1]
                    candidate = datetime(year, month, min(target_day, last_day), hh, mm)
                return candidate

            candidate = reference.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if candidate <= reference:
                candidate += timedelta(days=1)
            return candidate

        queued = 0
        queued_direct = 0
        queued_vpn_groups = 0
        initialized = 0
        skipped_not_ready = 0
        due_direct_devices = []
        due_vpn_by_group = defaultdict(lambda: {'tenant_id': None, 'device_ids': []})

        for schedule in schedules:
            if not schedule.next_run_at:
                schedule.next_run_at = _next_run(schedule, now)
                initialized += 1
                continue

            if schedule.next_run_at <= now:
                device = schedule.device
                ready_ok, ready_reason = _is_connection_ready_recent(device)
                if not ready_ok:
                    skipped_not_ready += 1
                    schedule.last_run_at = now
                    schedule.next_run_at = _next_run(schedule, now + timedelta(seconds=1))
                    logger.info(
                        "Agendamento ignorado (device=%s): %s",
                        str(getattr(device, "id", "")),
                        ready_reason,
                    )
                    continue
                if device and device.group and device.group.uses_vpn:
                    entry = due_vpn_by_group[str(device.group_id)]
                    entry['tenant_id'] = str(device.tenant_id)
                    entry['device_ids'].append(str(device.id))
                else:
                    due_direct_devices.append(str(schedule.device_id))
                schedule.last_run_at = now
                schedule.next_run_at = _next_run(schedule, now + timedelta(seconds=1))
                queued += 1

        for device_id in due_direct_devices:
            run_backup_task.delay(device_id)
            queued_direct += 1

        for group_id, data in due_vpn_by_group.items():
            unique_device_ids = sorted(set(data['device_ids']))
            run_vpn_group_backups_task.apply_async(
                args=[group_id, data['tenant_id'], unique_device_ids],
                queue='vpn_queue'
            )
            queued_vpn_groups += 1

        db.commit()

        if queued > 0 or initialized > 0:
            logger.info(
                f"Agendamentos processados: {len(schedules)} | enfileirados={queued} | inicializados={initialized}"
            )
        if skipped_not_ready > 0:
            logger.info(
                "Agendamentos ignorados por conectividade/credenciais pendentes: %s",
                skipped_not_ready,
            )

        return {
            'schedules_checked': len(schedules),
            'devices_queued': queued,
            'direct_devices_queued': queued_direct,
            'vpn_groups_queued': queued_vpn_groups,
            'initialized_next_run': initialized,
            'skipped_not_ready': skipped_not_ready,
        }
    finally:
        db.close()


@celery_app.task
def purge_expired_backups():
    """
    Remove backups expirados de acordo com a politica de retencao do plano.
    """
    from app.models.backup import Backup
    from app.models.device import Device
    from app.models.tenant import Tenant

    db = SessionLocal()

    try:
        tenants = db.query(Tenant).filter(Tenant.is_active == True).all()
        total_deleted = 0
        total_files_removed = 0

        for tenant in tenants:
            retention_days = settings.DEFAULT_RETENTION_DAYS
            if tenant.plan and tenant.plan.backup_retention_days:
                retention_days = tenant.plan.backup_retention_days

            cutoff = datetime.utcnow() - timedelta(days=retention_days)
            expired = db.query(Backup).join(Device).filter(
                Device.tenant_id == tenant.id,
                Backup.created_at < cutoff
            ).all()

            for backup in expired:
                if backup.file_path and os.path.exists(backup.file_path):
                    try:
                        os.remove(backup.file_path)
                        total_files_removed += 1
                    except OSError:
                        logger.warning(f"Falha ao remover arquivo: {backup.file_path}")
                db.delete(backup)
                total_deleted += 1

            db.commit()

        logger.info(f"Retencao aplicada: {total_deleted} backups removidos, {total_files_removed} arquivos deletados.")
        return {'deleted': total_deleted, 'files_removed': total_files_removed}
    finally:
        db.close()
