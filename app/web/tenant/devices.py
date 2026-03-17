from flask import Blueprint, render_template, redirect, url_for, request, flash, session, abort, jsonify
from app.web.auth.decorators import login_required, tenant_admin_required
from app.core.database import SessionLocal
from app.services.device_service import DeviceService, DeviceTypeService, DeviceGroupService
from app.models.tenant import Tenant
from app.models.device import Device
from app.models.device_type import DeviceType
from app.models.device_group import DeviceGroup
from app.models.backup import Backup, BackupStatus
from app.models.user import UserRole
from app.services.connection_test_service import connection_test_service
from app.tasks.monitoring import run_connection_test_task, run_device_connection_audit_task
from celery.exceptions import TimeoutError as CeleryTimeoutError
import uuid
import logging
from datetime import datetime, timezone, timedelta

bp = Blueprint('tenant_devices', __name__, url_prefix='/tenant/<tenant_slug>/devices')
MAX_READY_AGE_MINUTES = 30


def get_db_and_tenant(tenant_slug):
    # Cross-tenant check
    if session.get('user_role') != UserRole.SUPER_ADMIN.value and session.get('tenant_slug') != tenant_slug:
        abort(403)
        
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.slug == tenant_slug).first()
    return db, tenant


def _clear_global_force_stop_flag() -> bool:
    """Remove a trava global de parada forcada quando o operador inicia novo backup manual."""
    try:
        from app.services.realtime_backup_logs import get_redis_client
        r = get_redis_client()
        if not r:
            return False
        key = "backup_center:force_stop_backups"
        had_flag = str(r.get(key) or "").strip() == "1"
        if had_flag:
            r.delete(key)
            logging.getLogger(__name__).warning("Flag global de stop removida para novo backup manual.")
        return had_flag
    except Exception:
        logging.getLogger(__name__).exception("Falha ao limpar flag global de stop")
        return False


def _is_device_ready_recent(device, max_age_minutes: int = MAX_READY_AGE_MINUTES):
    from app.services.backup_diagnostics import is_connection_ready_recent
    return is_connection_ready_recent(
        getattr(device, "extra_parameters", None) or {},
        max_age_minutes=max_age_minutes,
    )


def _collect_ready_devices(devices, max_age_minutes: int = MAX_READY_AGE_MINUTES):
    ready = []
    stale = []
    for device in devices:
        ok, reason = _is_device_ready_recent(device, max_age_minutes=max_age_minutes)
        if ok:
            ready.append(device)
        else:
            stale.append((device, reason))
    return ready, stale


@bp.route('/')
@login_required
def list_devices(tenant_slug):
    db, tenant = get_db_and_tenant(tenant_slug)
    if not tenant:
        return "Tenant not found", 404
    
    # Filtro por grupo
    group_id = request.args.get('group_id')
    search_query = request.args.get('q')
    connection_filter = (request.args.get('connection') or '').strip().lower() or None
    auto_filter = (request.args.get('auto') or '').strip().lower() or None
    result_filter = (request.args.get('result') or '').strip().lower() or None
    history_filter = (request.args.get('history') or '').strip().lower() or None
    connection_audit_filter = (request.args.get('audit') or '').strip().lower() or None
    due_filter = (request.args.get('due') or '').strip() or None
    compare_mode = (request.args.get('compare') or '').strip() == '1'
    sort_by = (request.args.get('sort') or 'name_asc').strip().lower()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 30, type=int)
    per_page = max(10, min(per_page, 100))

    if connection_filter not in {None, 'online', 'offline', 'unknown'}:
        connection_filter = None
    if auto_filter not in {None, 'enabled', 'disabled'}:
        auto_filter = None
    if result_filter not in {None, 'success', 'failed', 'never'}:
        result_filter = None
    if history_filter not in {None, 'with_history', 'without_history'}:
        history_filter = None
    if connection_audit_filter not in {None, 'ping_ok', 'login_ok', 'ping_login_fail', 'no_ping'}:
        connection_audit_filter = None
    if due_filter not in {None, '1'}:
        due_filter = None
    if sort_by not in {'name_asc', 'name_desc', 'last_backup_desc', 'last_backup_asc', 'status_priority'}:
        sort_by = 'name_asc'

    if group_id:
        try:
            group_id = uuid.UUID(group_id)
        except ValueError:
            group_id = None

    result = DeviceService.get_tenant_devices(
        db,
        tenant.id,
        group_id,
        search_query,
        connection_filter=connection_filter,
        auto_filter=auto_filter,
        backup_result_filter=result_filter,
        history_filter=history_filter,
        connection_audit_filter=connection_audit_filter,
        due_filter=due_filter,
        sort_by=sort_by,
        page=page,
        per_page=per_page,
    )
    devices = result["items"]
    groups = DeviceGroupService.get_groups_with_device_count(db, tenant.id)
    device_types = DeviceTypeService.get_types_by_category(db)

    stats = {
        "total": result["total"],
        "scheduled": result["scheduled"],
        "online": result["online"],
        "with_issues": result["with_issues"],
        "offline": result.get("offline", 0),
        "without_history": result.get("without_history", 0),
        "auto_disabled": result.get("auto_disabled", 0),
    }
    total_pages = (result["total"] + per_page - 1) // per_page
    start_idx = ((page - 1) * per_page) + 1 if result["total"] > 0 else 0
    end_idx = min(page * per_page, result["total"])

    current_group = None
    if group_id:
        current_group = DeviceGroupService.get_group(db, group_id)

    db.close()
    return render_template(
        'tenant/devices/list.html',
        tenant=tenant,
        devices=devices,
        groups=groups,
        device_types=device_types,
        stats=stats,
        current_group=current_group,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        start_idx=start_idx,
        end_idx=end_idx,
        current_connection_filter=connection_filter,
        current_auto_filter=auto_filter,
        current_result_filter=result_filter,
        current_history_filter=history_filter,
        current_connection_audit_filter=connection_audit_filter,
        current_due_filter=due_filter,
        current_sort=sort_by,
        compare_mode=compare_mode,
    )

@bp.route('/add', methods=['GET', 'POST'])
@login_required
@tenant_admin_required
def add_device(tenant_slug):
    db, tenant = get_db_and_tenant(tenant_slug)
    if not tenant:
        return "Tenant not found", 404
    
    if request.method == 'POST':
        try:
            data = {
                'name': request.form.get('name'),
                'device_type_id': request.form.get('device_type_id') or None,
                'group_id': request.form.get('group_id') or None,
                'ip_address': request.form.get('ip_address'),
                'port': int(request.form.get('port', 22)),
                'username': request.form.get('username'),
                'password': request.form.get('password'),
                'description': request.form.get('description'),
                'use_telnet': request.form.get('use_telnet') == 'on',
                'backup_scheduled': request.form.get('backup_scheduled') == 'on',  # Checkbox value
                'schedule_frequency': request.form.get('schedule_frequency'),
                'schedule_time': request.form.get('schedule_time'),
            }
            device = DeviceService.create_device(db, tenant.id, data)
            
            # LOG ACTIVITY: Create Device
            from app.services.activity_service import ActivityService
            user_id = session.get('user_id')
            ActivityService.log_action(db, tenant.id, user_id, "CREATE_DEVICE", f"Created device: {device.name} ({device.ip_address})", request.remote_addr)

            flash('Dispositivo adicionado com sucesso!', 'success')
            return redirect(url_for('tenant_devices.list_devices', tenant_slug=tenant_slug))
        except Exception as e:
            flash(f'Erro ao adicionar dispositivo: {str(e)}', 'error')
        finally:
            db.close()
    
    groups = DeviceGroupService.get_tenant_groups(db, tenant.id)
    device_types = DeviceTypeService.get_all_types(db)
    
    return render_template(
        'tenant/devices/add.html',
        tenant=tenant,
        groups=groups,
        device_types=device_types
    )


@bp.route('/<device_id>')
@login_required
def view_device(tenant_slug, device_id):
    db, tenant = get_db_and_tenant(tenant_slug)
    if not tenant:
        return "Tenant not found", 404
    
    try:
        device_uuid = uuid.UUID(device_id)
    except ValueError:
        return "Invalid device ID", 400
    
    device = DeviceService.get_device(db, device_uuid)
    if not device or str(device.tenant_id) != str(tenant.id):
        db.close()
        return "Device not found", 404
    
    # Busca Ãºltimos backups
    from app.models.backup import Backup
    backups = db.query(Backup).filter(Backup.device_id == device.id).order_by(Backup.created_at.desc()).limit(10).all()
    
    db.close()
    return render_template(
        'tenant/devices/view.html',
        tenant=tenant,
        device=device,
        backups=backups
    )


@bp.route('/<device_id>/edit', methods=['GET', 'POST'])
@login_required
@tenant_admin_required
def edit_device(tenant_slug, device_id):
    db, tenant = get_db_and_tenant(tenant_slug)
    if not tenant:
        return "Tenant not found", 404
    
    try:
        device_uuid = uuid.UUID(device_id)
    except ValueError:
        return "Invalid device ID", 400
    
    device = DeviceService.get_device(db, device_uuid)
    if not device or str(device.tenant_id) != str(tenant.id):
        db.close()
        return "Device not found", 404
    
    if request.method == 'POST':
        try:
            data = {
                'name': request.form.get('name'),
                'device_type_id': request.form.get('device_type_id') or None,
                'group_id': request.form.get('group_id') or None,
                'ip_address': request.form.get('ip_address'),
                'port': int(request.form.get('port', 22)),
                'username': request.form.get('username'),
                'description': request.form.get('description'),
                'use_telnet': request.form.get('use_telnet') == 'on',
                'backup_scheduled': request.form.get('backup_scheduled') == 'on',
                'schedule_frequency': request.form.get('schedule_frequency'),
                'schedule_time': request.form.get('schedule_time'),
            }
            # SÃ³ atualiza senha se foi fornecida
            password = request.form.get('password')
            if password:
                data['password'] = password
            
            DeviceService.update_device(db, device_uuid, data)
            
            # LOG ACTIVITY: Update Device
            from app.services.activity_service import ActivityService
            user_id = session.get('user_id')
            ActivityService.log_action(db, tenant.id, user_id, "UPDATE_DEVICE", f"Updated device: {device.name}", request.remote_addr)

            flash('Dispositivo atualizado com sucesso!', 'success')
            return redirect(url_for('tenant_devices.view_device', tenant_slug=tenant_slug, device_id=device_id))
        except Exception as e:
            flash(f'Erro ao atualizar dispositivo: {str(e)}', 'error')
    
    groups = DeviceGroupService.get_tenant_groups(db, tenant.id)
    device_types = DeviceTypeService.get_all_types(db)
    
    db.close()
    return render_template(
        'tenant/devices/edit.html',
        tenant=tenant,
        device=device,
        groups=groups,
        device_types=device_types
    )


@bp.route('/<device_id>/delete', methods=['POST'])
@login_required
@tenant_admin_required
def delete_device(tenant_slug, device_id):
    db, tenant = get_db_and_tenant(tenant_slug)
    if not tenant:
        return "Tenant not found", 404
    
    try:
        device_uuid = uuid.UUID(device_id)
    except ValueError:
        flash('ID de dispositivo invÃ¡lido', 'error')
        return redirect(url_for('tenant_devices.list_devices', tenant_slug=tenant_slug))
    
    # Busca nome antes de deletar para log
    device = db.query(Device).filter(Device.id == device_uuid).first()
    device_name = device.name if device else "Unknown"

    if DeviceService.delete_device(db, device_uuid):
        # LOG ACTIVITY: Delete Device
        from app.services.activity_service import ActivityService
        user_id = session.get('user_id')
        ActivityService.log_action(db, tenant.id, user_id, "DELETE_DEVICE", f"Deleted device: {device_name}", request.remote_addr)

        flash('Dispositivo removido com sucesso!', 'success')
    else:
        flash('Erro ao remover dispositivo', 'error')
    
    db.close()
    return redirect(url_for('tenant_devices.list_devices', tenant_slug=tenant_slug))


@bp.route('/<device_id>/run', methods=['POST'])
@login_required
def run_backup(tenant_slug, device_id):
    """Executa backup de um dispositivo especÃ­fico."""
    from app.tasks.backups import run_backup_task, run_vpn_group_backups_task
    from app.services.realtime_backup_logs import register_task, append_task_log
    
    db, tenant = get_db_and_tenant(tenant_slug)
    if not tenant:
        return "Tenant not found", 404

    force_stop_cleared = _clear_global_force_stop_flag()
    
    try:
        device_uuid = uuid.UUID(device_id)
    except ValueError:
        flash('ID de dispositivo invÃ¡lido', 'error')
        return redirect(url_for('tenant_devices.list_devices', tenant_slug=tenant_slug))
    
    # Busca nome para log
    device = db.query(Device).filter(Device.id == device_uuid).first()
    
    if not device:
        flash('Dispositivo nÃ£o encontrado', 'error')
        db.close()
        return redirect(url_for('tenant_devices.list_devices', tenant_slug=tenant_slug))

    device_name = device.name

    if force_stop_cleared:
        flash('Bloqueio global de parada foi removido automaticamente para iniciar novo backup.', 'warning')

    # Enfileira backup (direto ou VPN)
    if device.group and device.group.uses_vpn:
        task = run_vpn_group_backups_task.apply_async(
            args=[str(device.group_id), str(tenant.id), [str(device.id)]],
            queue='vpn_queue'
        )
        queue_mode = 'vpn_queue'
    else:
        task = run_backup_task.delay(str(device_uuid))
        queue_mode = 'celery'

    register_task(
        task_id=str(task.id),
        tenant_id=str(tenant.id),
        device_id=str(device.id),
        device_name=device_name,
        group_id=str(device.group_id) if device.group_id else None,
    )
    append_task_log(
        str(task.id),
        device_name,
        f"Task criada na fila {queue_mode}. Aguardando worker iniciar.",
        "info",
    )

    # LOG ACTIVITY: Manual Backup (queued)
    from app.services.activity_service import ActivityService
    user_id = session.get('user_id')
    details = (
        f"Manual backup queued for {device_name}. "
        f"task_id={task.id} queue={queue_mode}"
    )
    ActivityService.log_action(db, tenant.id, user_id, "BACKUP_MANUAL", details, request.remote_addr)
    is_ajax = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in (request.headers.get('Accept') or '')
    )
    if is_ajax:
        db.close()
        return jsonify({
            'ok': True,
            'task_id': str(task.id),
            'queue': queue_mode,
            'device_name': device_name,
            'status_url': url_for('tenant_backups.task_status', tenant_slug=tenant_slug, task_id=str(task.id)),
            'logs_url': url_for('tenant_backups.task_logs', tenant_slug=tenant_slug, task_id=str(task.id)),
        }), 202

    flash(f'Backup enfileirado com sucesso! Task: {task.id}', 'success')
    
    db.close()
    return redirect(url_for('tenant_devices.list_devices', tenant_slug=tenant_slug))


@bp.route('/<device_id>/test-connection', methods=['POST'])
@login_required
def test_connection(tenant_slug, device_id):
    """Valida conexao/autenticacao do dispositivo sem executar backup."""
    db, tenant = get_db_and_tenant(tenant_slug)
    if not tenant:
        return "Tenant not found", 404

    try:
        device_uuid = uuid.UUID(device_id)
    except ValueError:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            db.close()
            return jsonify({'ok': False, 'error': 'ID de dispositivo invalido.'}), 400
        flash('ID de dispositivo invalido', 'error')
        db.close()
        return redirect(url_for('tenant_devices.list_devices', tenant_slug=tenant_slug))

    device = db.query(Device).filter(
        Device.id == device_uuid,
        Device.tenant_id == tenant.id,
    ).first()
    if not device:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            db.close()
            return jsonify({'ok': False, 'error': 'Dispositivo nao encontrado.'}), 404
        flash('Dispositivo nao encontrado', 'error')
        db.close()
        return redirect(url_for('tenant_devices.list_devices', tenant_slug=tenant_slug))

    device_name = device.name
    device_ip = device.ip_address
    device_port = device.port

    payload = None
    try:
        test_task = run_connection_test_task.apply_async(
            args=[str(device.id)],
            queue='vpn_queue',
        )
        payload = test_task.get(timeout=180)
    except CeleryTimeoutError:
        payload = {
            'ok': False,
            'message': 'Timeout no teste de conexao (worker VPN).',
            'protocol': 'ssh' if not device.use_telnet else 'telnet',
            'elapsed_ms': 180000,
        }
    except Exception as exc:
        payload = {
            'ok': False,
            'message': f'Erro ao executar teste de conexao: {exc}',
            'protocol': 'ssh' if not device.use_telnet else 'telnet',
            'elapsed_ms': 0,
        }

    test_success = bool(payload.get('ok'))
    test_message = payload.get('message') or payload.get('error') or 'Falha de conexao.'
    test_protocol = payload.get('protocol') or ('ssh' if not device.use_telnet else 'telnet')
    test_elapsed = int(payload.get('elapsed_ms') or 0)

    from app.services.activity_service import ActivityService
    user_id = session.get('user_id')
    status_text = "SUCCESS" if test_success else "FAILED"
    details = (
        f"Connection test {status_text} for {device_name} "
        f"({device_ip}:{device_port}) protocol={test_protocol} "
        f"elapsed_ms={test_elapsed} msg={test_message}"
    )
    ActivityService.log_action(db, tenant.id, user_id, "TEST_CONNECTION", details, request.remote_addr)

    is_ajax = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in (request.headers.get('Accept') or '')
    )
    if is_ajax:
        db.close()
        return jsonify({
            'ok': test_success,
            'device_name': device_name,
            'message': test_message,
            'protocol': test_protocol,
            'elapsed_ms': test_elapsed,
        }), (200 if test_success else 422)

    if test_success:
        flash(f"Conexao com {device_name} validada com sucesso ({test_protocol.upper()}).", 'success')
    else:
        flash(f"Falha no teste de conexao de {device_name}: {test_message}", 'error')
    db.close()
    return redirect(url_for('tenant_devices.view_device', tenant_slug=tenant_slug, device_id=device_id))

@bp.route('/run-all', methods=['POST'])
@login_required
def run_backup_all(tenant_slug):
    """Executa backup de todos os dispositivos agendados."""
    from celery import chord
    from app.tasks.backups import (
        run_backup_task,
        run_vpn_group_backups_task,
        enqueue_vpn_groups_after_direct_phase_task,
    )
    from app.services.realtime_backup_logs import register_task, update_task_meta, append_task_log
    
    db, tenant = get_db_and_tenant(tenant_slug)
    if not tenant:
        return "Tenant not found", 404

    force_stop_cleared = _clear_global_force_stop_flag()
    
    is_ajax = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in (request.headers.get('Accept') or '')
    )

    include_unvalidated = (request.form.get("include_unvalidated") or "").strip().lower() in {"1", "true", "on", "yes"}
    only_ready = not include_unvalidated

    # Pega o grupo se especificado
    group_id = request.form.get('group_id')
    
    # Busca dispositivos agendados
    query = db.query(Device).filter(
        Device.tenant_id == tenant.id,
        Device.backup_scheduled == True,
        Device.is_active == True,
    )
    
    if group_id:
        try:
            group_uuid = uuid.UUID(group_id)
            query = query.filter(Device.group_id == group_uuid)
        except ValueError:
            pass
    
    base_devices = query.all()
    skipped_not_ready = 0
    if only_ready:
        devices, stale = _collect_ready_devices(base_devices, max_age_minutes=MAX_READY_AGE_MINUTES)
        skipped_not_ready = len(stale)
    else:
        devices = base_devices
    if not devices:
        if is_ajax:
            db.close()
            if only_ready:
                return jsonify({'ok': False, 'error': f'Nenhum dispositivo apto com ping+login OK recente (<= {MAX_READY_AGE_MINUTES} min).'}), 400
            return jsonify({'ok': False, 'error': 'Nenhum dispositivo agendado encontrado.'}), 400
        if only_ready:
            flash(f'Nenhum dispositivo apto com ping+login OK recente (<= {MAX_READY_AGE_MINUTES} min).', 'warning')
        else:
            flash('Nenhum dispositivo agendado encontrado', 'warning')
        db.close()
        return redirect(url_for('tenant_devices.list_devices', tenant_slug=tenant_slug))
    
    queued_direct = 0
    queued_vpn_groups = 0
    vpn_by_group = {}
    vpn_payload = []
    vpn_phase_deferred = False
    group_summary = {}
    direct_signatures = []
    child_task_ids = []
    child_task_device_count = {}
    bulk_task_id = f"bulk-{uuid.uuid4()}" if is_ajax else None

    if is_ajax:
        register_task(
            task_id=bulk_task_id,
            tenant_id=str(tenant.id),
            device_name="Backup em massa",
            group_id=str(group_id) if group_id else None,
        )
        if force_stop_cleared:
            append_task_log(
                bulk_task_id,
                "Backup em massa",
                "Bloqueio global anterior removido automaticamente para iniciar novo lote.",
                "warning",
            )
            append_task_log(
                bulk_task_id,
                "Backup em massa",
                f"Iniciando enfileiramento para {len(devices)} dispositivos agendados.",
                "info",
            )
            if skipped_not_ready > 0:
                append_task_log(
                    bulk_task_id,
                    "Backup em massa",
                    (
                        f"{skipped_not_ready} dispositivo(s) foram pulados por nao estarem "
                        f"com ping+login OK recente (<= {MAX_READY_AGE_MINUTES} min)."
                    ),
                    "warning",
                )

    for device in devices:
        group_name = device.group.name if device.group else "Sem grupo"
        if group_name not in group_summary:
            group_summary[group_name] = {
                "group_name": group_name,
                "connection_mode": "vpn" if (device.group and device.group.uses_vpn) else "direct",
                "devices": 0,
            }
        group_summary[group_name]["devices"] += 1

        if device.group and device.group.uses_vpn:
            vpn_by_group.setdefault(str(device.group_id), []).append(str(device.id))
        else:
            # Suaviza picos de conexao no lote para evitar tempestade de autenticacoes simultaneas.
            countdown = queued_direct // 25 if is_ajax else 0
            if is_ajax:
                direct_task_id = str(uuid.uuid4())
                task_sig = run_backup_task.s(str(device.id), bulk_task_id).set(
                    task_id=direct_task_id,
                    countdown=countdown,
                    queue='celery',
                )
                direct_signatures.append(task_sig)
                child_task_ids.append(direct_task_id)
                child_task_device_count[direct_task_id] = 1
                register_task(
                    task_id=direct_task_id,
                    tenant_id=str(tenant.id),
                    device_id=str(device.id),
                    device_name=device.name,
                    group_id=str(device.group_id) if device.group_id else None,
                )
            else:
                run_backup_task.delay(str(device.id))
            queued_direct += 1

    for group_uuid, grouped_ids in vpn_by_group.items():
        unique_ids = sorted(set(grouped_ids))
        if not unique_ids:
            continue
        vpn_payload.append({
            'group_id': str(group_uuid),
            'device_ids': unique_ids,
        })

    queued_vpn_groups = len(vpn_payload)

    # Fase 1: dispositivos sem VPN.
    # Fase 2: grupos VPN somente após término da fase direta.
    if is_ajax and direct_signatures:
        if vpn_payload:
            callback_sig = enqueue_vpn_groups_after_direct_phase_task.s(
                str(tenant.id),
                vpn_payload,
                bulk_task_id,
            ).set(queue='celery')
            chord(direct_signatures)(callback_sig)
            vpn_phase_deferred = True
            append_task_log(
                bulk_task_id,
                "Backup em massa",
                (
                    f"Fase 1 enfileirada com {queued_direct} dispositivos diretos. "
                    f"Fase 2 com {queued_vpn_groups} grupo(s) VPN sera iniciada somente ao final da fase 1."
                ),
                "info",
            )
        else:
            for sig in direct_signatures:
                sig.apply_async()

    if not vpn_phase_deferred:
        for item in vpn_payload:
            vpn_args = [item['group_id'], str(tenant.id), item['device_ids']]
            if is_ajax:
                vpn_args.append(bulk_task_id)
            task = run_vpn_group_backups_task.apply_async(
                args=vpn_args,
                queue='vpn_queue'
            )
            child_task_ids.append(str(task.id))
            child_task_device_count[str(task.id)] = len(item['device_ids'])
            if is_ajax:
                register_task(
                    task_id=str(task.id),
                    tenant_id=str(tenant.id),
                    device_name=f"Grupo VPN {item['group_id']}",
                    group_id=str(item['group_id']),
                )
    
    # LOG ACTIVITY
    from app.services.activity_service import ActivityService
    user_id = session.get('user_id')
    details = (
        f"Bulk backup queued: {queued_direct} dispositivos diretos e "
        f"{queued_vpn_groups} grupo(s) VPN ({len(devices)} total). "
        f"vpn_deferred={'yes' if vpn_phase_deferred else 'no'}"
    )
    ActivityService.log_action(db, tenant.id, user_id, "BACKUP_BULK", details, request.remote_addr)
    
    if is_ajax:
        summary_rows = sorted(group_summary.values(), key=lambda row: (-row["devices"], row["group_name"].lower()))
        total_tasks = len(child_task_ids) + (queued_vpn_groups if vpn_phase_deferred else 0)
        update_task_meta(
            bulk_task_id,
            is_bulk=True,
            operation_kind="backup_bulk",
            status='running',
            progress=5,
            completed=False,
            cancel_requested=False,
            message=(
                f"{total_tasks} tarefas planejadas para processamento."
                if vpn_phase_deferred
                else f"{len(child_task_ids)} tarefas enfileiradas para processamento."
            ),
            total_devices=len(devices),
            queued_direct=queued_direct,
            queued_vpn_groups=queued_vpn_groups,
            total_tasks=total_tasks,
            done_tasks=0,
            success_tasks=0,
            failed_tasks=0,
            running_tasks=0,
            queued_tasks=total_tasks,
            child_task_ids=child_task_ids,
            child_task_device_count=child_task_device_count,
            finished_task_ids=[],
            group_summary=summary_rows,
            skipped_not_ready=skipped_not_ready,
        )
        if vpn_phase_deferred:
            append_task_log(
                bulk_task_id,
                "Backup em massa",
                (
                    f"Enfileirado fase 1: {queued_direct} diretos. "
                    f"Fase 2: {queued_vpn_groups} grupo(s) VPN aguardando callback."
                ),
                "success",
            )
        else:
            append_task_log(
                bulk_task_id,
                "Backup em massa",
                (
                    f"Enfileirado: {queued_direct} diretos + {queued_vpn_groups} grupos VPN "
                    f"({len(child_task_ids)} tasks Celery)."
                ),
                "success",
            )
        db.close()
        return jsonify({
            'ok': True,
            'is_bulk': True,
            'task_id': bulk_task_id,
            'device_name': "Backup em massa",
            'queued_direct': queued_direct,
            'queued_vpn_groups': queued_vpn_groups,
            'total_devices': len(devices),
            'total_tasks': total_tasks,
            'operation_kind': 'backup_bulk',
            'group_summary': summary_rows,
            'skipped_not_ready': skipped_not_ready,
            'status_url': url_for('tenant_backups.bulk_task_status', tenant_slug=tenant_slug, task_id=bulk_task_id),
            'logs_url': url_for('tenant_backups.bulk_task_logs', tenant_slug=tenant_slug, task_id=bulk_task_id),
            'cancel_url': url_for('tenant_backups.cancel_bulk_task', tenant_slug=tenant_slug, task_id=bulk_task_id),
        }), 202

    db.close()
    if force_stop_cleared:
        flash('Bloqueio global de parada foi removido automaticamente para iniciar este lote.', 'warning')
    if vpn_phase_deferred:
        flash(
            f'Fase 1 enfileirada: {queued_direct} diretos. '
            f'Fase 2 (VPN): {queued_vpn_groups} grupo(s) iniciara apos finalizar a fase 1.',
            'success'
        )
    else:
        flash(
            f'Backups enfileirados: {queued_direct} diretos + {queued_vpn_groups} grupo(s) VPN.',
            'success'
        )
    if skipped_not_ready > 0:
        flash(
            f'{skipped_not_ready} dispositivo(s) pulados por nao estarem com ping+login OK recente (<= {MAX_READY_AGE_MINUTES} min).',
            'warning'
        )
    
    return redirect(url_for('tenant_devices.list_devices', tenant_slug=tenant_slug))


@bp.route('/run-connection-audit-all', methods=['POST'])
@login_required
def run_connection_audit_all(tenant_slug):
    """Executa teste em massa de ping + login (sem backup)."""
    from app.services.realtime_backup_logs import register_task, update_task_meta, append_task_log

    db, tenant = get_db_and_tenant(tenant_slug)
    if not tenant:
        return "Tenant not found", 404

    is_ajax = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in (request.headers.get('Accept') or '')
    )

    group_id = request.form.get('group_id')
    query = db.query(Device).filter(
        Device.tenant_id == tenant.id,
        Device.is_active == True,
    )
    if group_id:
        try:
            group_uuid = uuid.UUID(group_id)
            query = query.filter(Device.group_id == group_uuid)
        except ValueError:
            pass

    devices = query.all()
    if not devices:
        if is_ajax:
            db.close()
            return jsonify({'ok': False, 'error': 'Nenhum dispositivo ativo encontrado para teste.'}), 400
        flash('Nenhum dispositivo ativo encontrado para teste.', 'warning')
        db.close()
        return redirect(url_for('tenant_schedules.list_schedules', tenant_slug=tenant_slug))

    queued = 0
    queued_direct = 0
    queued_vpn = 0
    child_task_ids = []
    child_task_device_count = {}
    bulk_task_id = f"bulk-{uuid.uuid4()}"

    register_task(
        task_id=bulk_task_id,
        tenant_id=str(tenant.id),
        device_name="Teste ping/login em massa",
        group_id=str(group_id) if group_id else None,
    )
    append_task_log(
        bulk_task_id,
        "Teste ping/login",
        f"Iniciando enfileiramento para {len(devices)} dispositivo(s) ativo(s).",
        "info",
    )

    for device in devices:
        child_id = str(uuid.uuid4())
        target_queue = 'vpn_queue' if (device.group and device.group.uses_vpn) else 'celery'
        run_device_connection_audit_task.apply_async(
            args=[str(device.id), bulk_task_id],
            task_id=child_id,
            queue=target_queue,
        )
        queued += 1
        if target_queue == 'vpn_queue':
            queued_vpn += 1
        else:
            queued_direct += 1
        child_task_ids.append(child_id)
        child_task_device_count[child_id] = 1
        register_task(
            task_id=child_id,
            tenant_id=str(tenant.id),
            device_id=str(device.id),
            device_name=device.name,
            group_id=str(device.group_id) if device.group_id else None,
        )

    update_task_meta(
        bulk_task_id,
        is_bulk=True,
        operation_kind="connection_audit",
        status='running',
        progress=5,
        completed=False,
        cancel_requested=False,
        message=f"{queued} testes planejados para processamento.",
        total_devices=len(devices),
        queued_direct=queued_direct,
        queued_vpn_groups=queued_vpn,
        total_tasks=queued,
        done_tasks=0,
        success_tasks=0,
        failed_tasks=0,
        running_tasks=0,
        queued_tasks=queued,
        done_devices=0,
        success_devices=0,
        failed_devices=0,
        no_ping_devices=0,
        ping_ok_login_fail_devices=0,
        ping_login_ok_devices=0,
        child_task_ids=child_task_ids,
        child_task_device_count=child_task_device_count,
        finished_task_ids=[],
        group_summary=[],
    )
    append_task_log(
        bulk_task_id,
        "Teste ping/login",
        f"Enfileirado: {queued} tarefa(s) de validacao de acesso.",
        "success",
    )

    payload = {
        'ok': True,
        'is_bulk': True,
        'task_id': bulk_task_id,
        'device_name': "Teste ping/login em massa",
        'queued_direct': queued_direct,
        'queued_vpn_groups': queued_vpn,
        'total_devices': len(devices),
        'total_tasks': queued,
        'operation_kind': 'connection_audit',
        'status_url': url_for('tenant_backups.bulk_task_status', tenant_slug=tenant_slug, task_id=bulk_task_id),
        'logs_url': url_for('tenant_backups.bulk_task_logs', tenant_slug=tenant_slug, task_id=bulk_task_id),
        'cancel_url': url_for('tenant_backups.cancel_bulk_task', tenant_slug=tenant_slug, task_id=bulk_task_id),
    }

    db.close()
    if is_ajax:
        return jsonify(payload), 202

    flash(f'Teste ping/login enfileirado para {queued} dispositivo(s).', 'success')
    return redirect(url_for('tenant_schedules.list_schedules', tenant_slug=tenant_slug))


@bp.route('/run-selected', methods=['POST'])
@login_required
def run_backup_selected(tenant_slug):
    """Executa backup dos dispositivos selecionados."""
    from app.tasks.backups import run_backup_task, run_vpn_group_backups_task
    
    db, tenant = get_db_and_tenant(tenant_slug)
    if not tenant:
        return "Tenant not found", 404

    force_stop_cleared = _clear_global_force_stop_flag()
    
    # Pega IDs selecionados
    device_ids = request.form.getlist('device_ids')
    
    if not device_ids:
        flash('Nenhum dispositivo selecionado', 'warning')
        return redirect(url_for('tenant_devices.list_devices', tenant_slug=tenant_slug))
    
    include_unvalidated = (request.form.get("include_unvalidated") or "").strip().lower() in {"1", "true", "on", "yes"}
    queued_direct = 0
    invalid_count = 0
    skipped_not_ready = 0
    vpn_by_group = {}
    
    for device_id in device_ids:
        try:
            device_uuid = uuid.UUID(device_id)
            # Verifica se pertence ao tenant
            device = db.query(Device).filter(
                Device.id == device_uuid,
                Device.tenant_id == tenant.id
            ).first()
            
            if device:
                if not include_unvalidated:
                    ready_ok, _ = _is_device_ready_recent(device, max_age_minutes=MAX_READY_AGE_MINUTES)
                    if not ready_ok:
                        skipped_not_ready += 1
                        continue
                if device.group and device.group.uses_vpn:
                    vpn_by_group.setdefault(str(device.group_id), []).append(str(device.id))
                else:
                    run_backup_task.delay(str(device.id))
                    queued_direct += 1
        except ValueError:
            invalid_count += 1

    queued_vpn_groups = 0
    for group_uuid, grouped_ids in vpn_by_group.items():
        run_vpn_group_backups_task.apply_async(
            args=[group_uuid, str(tenant.id), sorted(set(grouped_ids))],
            queue='vpn_queue'
        )
        queued_vpn_groups += 1
    
    # LOG ACTIVITY
    from app.services.activity_service import ActivityService
    user_id = session.get('user_id')
    details = (
        f"Selected backup queued: {queued_direct} diretos, "
        f"{queued_vpn_groups} grupo(s) VPN, invalidos={invalid_count}, "
        f"pulados_not_ready={skipped_not_ready}."
    )
    ActivityService.log_action(db, tenant.id, user_id, "BACKUP_SELECTED", details, request.remote_addr)
    
    db.close()

    if force_stop_cleared:
        flash('Bloqueio global de parada foi removido automaticamente para iniciar os backups selecionados.', 'warning')

    total_queued = queued_direct + queued_vpn_groups
    if total_queued <= 0:
        flash(f'Nenhum dispositivo apto com ping+login OK recente (<= {MAX_READY_AGE_MINUTES} min).', 'warning')
    else:
        flash(
            f'Backups selecionados enfileirados: {queued_direct} diretos + {queued_vpn_groups} grupo(s) VPN.',
            'success'
        )
    if skipped_not_ready > 0:
        flash(
            f'{skipped_not_ready} dispositivo(s) selecionados foram pulados por falta de teste ping+login recente.',
            'warning'
        )
    
    return redirect(url_for('tenant_devices.list_devices', tenant_slug=tenant_slug))


@bp.route('/run-reprocess-failures', methods=['POST'])
@login_required
def run_reprocess_failures(tenant_slug):
    """Reprocessa dispositivos com falhas recentes (hoje ou ultimas 24h)."""
    from app.tasks.backups import run_backup_task, run_vpn_group_backups_task
    from app.services.realtime_backup_logs import register_task, update_task_meta, append_task_log
    from app.services.backup_diagnostics import classify_failure, is_transient_failure

    db, tenant = get_db_and_tenant(tenant_slug)
    if not tenant:
        return "Tenant not found", 404

    is_ajax = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in (request.headers.get('Accept') or '')
    )
    scope = (request.form.get('scope') or 'today').strip().lower()
    transient_only = (request.form.get('transient_only') or '').strip().lower() in {"1", "true", "on", "yes"}
    include_unvalidated = (request.form.get("include_unvalidated") or "").strip().lower() in {"1", "true", "on", "yes"}

    now = datetime.utcnow()
    if scope == '24h':
        since = now - timedelta(hours=24)
        scope_label = "ultimas 24h"
    else:
        since = datetime(now.year, now.month, now.day, 0, 0, 0)
        scope_label = "hoje"

    failed_rows = (
        db.query(Backup)
        .join(Device, Device.id == Backup.device_id)
        .filter(
            Device.tenant_id == tenant.id,
            Device.is_active == True,
            Device.backup_scheduled == True,
            Backup.status == BackupStatus.FAILED,
            Backup.started_at.isnot(None),
            Backup.started_at >= since,
        )
        .order_by(Backup.started_at.desc())
        .all()
    )

    latest_failed_by_device = {}
    for row in failed_rows:
        key = str(row.device_id)
        if key in latest_failed_by_device:
            continue
        category = classify_failure(row.error_message or "")
        if transient_only and not is_transient_failure(category):
            continue
        latest_failed_by_device[key] = {
            "device_id": key,
            "category": category,
            "backup_id": str(row.id),
            "started_at": row.started_at.isoformat() if row.started_at else None,
        }

    if not latest_failed_by_device:
        msg = (
            f"Nenhuma falha {'transitoria ' if transient_only else ''}encontrada para {scope_label}."
        )
        if is_ajax:
            db.close()
            return jsonify({"ok": False, "error": msg}), 400
        flash(msg, "warning")
        db.close()
        return redirect(url_for('tenant_schedules.list_schedules', tenant_slug=tenant_slug))

    target_device_ids = [uuid.UUID(v["device_id"]) for v in latest_failed_by_device.values()]
    devices = (
        db.query(Device)
        .filter(
            Device.tenant_id == tenant.id,
            Device.id.in_(target_device_ids),
            Device.is_active == True,
            Device.backup_scheduled == True,
        )
        .all()
    )

    skipped_not_ready = 0
    if not include_unvalidated:
        devices, stale = _collect_ready_devices(devices, max_age_minutes=MAX_READY_AGE_MINUTES)
        skipped_not_ready = len(stale)

    if not devices:
        if is_ajax:
            db.close()
            return jsonify({"ok": False, "error": "Nenhum dispositivo apto com ping+login recente para reprocessar."}), 400
        flash("Nenhum dispositivo apto com ping+login recente para reprocessar.", "warning")
        db.close()
        return redirect(url_for('tenant_schedules.list_schedules', tenant_slug=tenant_slug))

    queued_direct = 0
    queued_vpn_groups = 0
    vpn_by_group = {}
    child_task_ids = []
    child_task_device_count = {}
    bulk_task_id = f"bulk-{uuid.uuid4()}"

    register_task(
        task_id=bulk_task_id,
        tenant_id=str(tenant.id),
        device_name="Reprocessamento de falhas",
        group_id=None,
    )
    append_task_log(
        bulk_task_id,
        "Reprocessamento",
        (
            f"Iniciando reprocessamento ({scope_label}) para {len(devices)} dispositivo(s) "
            f"{'com falhas transitorias' if transient_only else 'com falha'}."
        ),
        "info",
    )
    if skipped_not_ready > 0:
        append_task_log(
            bulk_task_id,
            "Reprocessamento",
            (
                f"{skipped_not_ready} dispositivo(s) pulados por nao estarem com "
                f"ping+login OK recente (<= {MAX_READY_AGE_MINUTES} min)."
            ),
            "warning",
        )

    for device in devices:
        if device.group and device.group.uses_vpn:
            vpn_by_group.setdefault(str(device.group_id), []).append(str(device.id))
            continue

        countdown = queued_direct // 25
        direct_task_id = str(uuid.uuid4())
        run_backup_task.apply_async(
            args=[str(device.id), bulk_task_id],
            task_id=direct_task_id,
            countdown=countdown,
            queue='celery',
        )
        queued_direct += 1
        child_task_ids.append(direct_task_id)
        child_task_device_count[direct_task_id] = 1
        register_task(
            task_id=direct_task_id,
            tenant_id=str(tenant.id),
            device_id=str(device.id),
            device_name=device.name,
            group_id=str(device.group_id) if device.group_id else None,
        )

    for group_uuid, grouped_ids in vpn_by_group.items():
        unique_ids = sorted(set(grouped_ids))
        if not unique_ids:
            continue
        task = run_vpn_group_backups_task.apply_async(
            args=[group_uuid, str(tenant.id), unique_ids, bulk_task_id],
            queue='vpn_queue',
        )
        queued_vpn_groups += 1
        child_task_ids.append(str(task.id))
        child_task_device_count[str(task.id)] = len(unique_ids)
        register_task(
            task_id=str(task.id),
            tenant_id=str(tenant.id),
            device_name=f"Grupo VPN {group_uuid}",
            group_id=group_uuid,
        )

    total_tasks = len(child_task_ids)
    update_task_meta(
        bulk_task_id,
        is_bulk=True,
        operation_kind="backup_reprocess",
        status='running',
        progress=5,
        completed=False,
        cancel_requested=False,
        message=f"{total_tasks} tarefas enfileiradas para reprocessamento.",
        total_devices=len(devices),
        queued_direct=queued_direct,
        queued_vpn_groups=queued_vpn_groups,
        total_tasks=total_tasks,
        done_tasks=0,
        success_tasks=0,
        failed_tasks=0,
        running_tasks=0,
        queued_tasks=total_tasks,
        child_task_ids=child_task_ids,
        child_task_device_count=child_task_device_count,
        finished_task_ids=[],
        group_summary=[],
        skipped_not_ready=skipped_not_ready,
    )

    append_task_log(
        bulk_task_id,
        "Reprocessamento",
        (
            f"Enfileirado: {queued_direct} diretos + {queued_vpn_groups} grupos VPN "
            f"({total_tasks} tasks Celery)."
        ),
        "success",
    )

    from app.services.activity_service import ActivityService
    user_id = session.get('user_id')
    ActivityService.log_action(
        db,
        tenant.id,
        user_id,
        "BACKUP_REPROCESS",
        (
            f"Reprocess queued ({scope_label}): transient_only={transient_only} "
            f"queued_direct={queued_direct} queued_vpn_groups={queued_vpn_groups} "
            f"skipped_not_ready={skipped_not_ready}"
        ),
        request.remote_addr,
    )

    response_payload = {
        "ok": True,
        "is_bulk": True,
        "task_id": bulk_task_id,
        "device_name": "Reprocessamento de falhas",
        "queued_direct": queued_direct,
        "queued_vpn_groups": queued_vpn_groups,
        "total_devices": len(devices),
        "total_tasks": total_tasks,
        "operation_kind": "backup_reprocess",
        "status_url": url_for('tenant_backups.bulk_task_status', tenant_slug=tenant_slug, task_id=bulk_task_id),
        "logs_url": url_for('tenant_backups.bulk_task_logs', tenant_slug=tenant_slug, task_id=bulk_task_id),
        "cancel_url": url_for('tenant_backups.cancel_bulk_task', tenant_slug=tenant_slug, task_id=bulk_task_id),
    }
    db.close()

    if is_ajax:
        return jsonify(response_payload), 202

    flash(
        f"Reprocessamento enfileirado: {queued_direct} diretos + {queued_vpn_groups} grupo(s) VPN.",
        "success",
    )
    if skipped_not_ready > 0:
        flash(
            f"{skipped_not_ready} dispositivo(s) pulados por falta de teste ping/login recente.",
            "warning",
        )
    return redirect(url_for('tenant_schedules.list_schedules', tenant_slug=tenant_slug))
