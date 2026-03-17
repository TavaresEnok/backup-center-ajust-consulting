from datetime import datetime, timedelta
import re
from collections import Counter
import uuid
import json

from flask import Blueprint, render_template, request, session, abort, redirect, url_for, flash, jsonify
from app.web.auth.decorators import login_required, tenant_admin_required
from app.core.database import SessionLocal
from app.models.tenant import Tenant
from app.models.schedule import Schedule, ScheduleFrequency
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.user import UserRole
from sqlalchemy.orm import joinedload
from app.services.activity_service import ActivityService
from app.celery_app import celery_app
from app.services.realtime_backup_logs import get_redis_client

bp = Blueprint('tenant_schedules', __name__, url_prefix='/tenant/<tenant_slug>/schedules')

def get_db_and_tenant(tenant_slug):
    if session.get('user_role') != UserRole.SUPER_ADMIN.value and session.get('tenant_slug') != tenant_slug:
        abort(403)
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.slug == tenant_slug).first()
    return db, tenant


def _next_daily_run(time_str: str, now: datetime | None = None) -> datetime:
    now = now or datetime.utcnow()
    hh, mm = map(int, time_str.split(":"))
    candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _is_valid_time(value: str) -> bool:
    return bool(re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", value or ""))


def _resolve_return_url(tenant_slug: str, return_to: str | None):
    if (return_to or "").strip() == "operations":
        return url_for("tenant_operations.index", tenant_slug=tenant_slug)
    return url_for("tenant_schedules.list_schedules", tenant_slug=tenant_slug)


def _apply_daily_to_devices(db, devices, time_str: str, apply_scope: str) -> int:
    if not devices:
        return 0

    device_ids = [d.id for d in devices]
    existing = db.query(Schedule).filter(Schedule.device_id.in_(device_ids)).all()
    schedules_by_device = {}
    active_schedule_device_ids = set()
    for schedule in existing:
        current = schedules_by_device.get(schedule.device_id)
        if not current or (not current.is_active and schedule.is_active):
            schedules_by_device[schedule.device_id] = schedule
        if schedule.is_active:
            active_schedule_device_ids.add(schedule.device_id)

    if apply_scope == "missing":
        target_devices = [
            device for device in devices
            if not (device.backup_scheduled and device.id in active_schedule_device_ids)
        ]
    else:
        target_devices = devices

    for device in target_devices:
        device.backup_scheduled = True
        schedule = schedules_by_device.get(device.id)
        if not schedule:
            schedule = Schedule(device_id=device.id)
            db.add(schedule)

        schedule.frequency = ScheduleFrequency.DAILY
        schedule.time = time_str
        schedule.day_of_week = None
        schedule.day_of_month = None
        schedule.is_active = True
        schedule.next_run_at = _next_daily_run(time_str)

    return len(target_devices)


@bp.route('/')
@login_required
def list_schedules(tenant_slug):
    db, tenant = get_db_and_tenant(tenant_slug)
    if not tenant:
        db.close()
        return "Tenant not found", 404
        
    show_details = request.args.get('details') == '1'
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    missing_page = request.args.get('missing_page', 1, type=int)
    missing_per_page = request.args.get('missing_per_page', 20, type=int)
    page = max(page, 1)
    per_page = max(10, min(per_page, 100))
    missing_page = max(missing_page, 1)
    missing_per_page = max(10, min(missing_per_page, 100))

    schedules_base_query = (
        db.query(Schedule)
        .join(Device)
        .filter(Device.tenant_id == tenant.id)
    )

    schedule_total = schedules_base_query.count()
    schedule_active = schedules_base_query.filter(Schedule.is_active == True).count()
    schedule_paused = schedule_total - schedule_active

    schedules = []
    total_pages = 1
    start_idx = 0
    end_idx = 0
    if show_details:
        total_pages = (schedule_total + per_page - 1) // per_page if schedule_total > 0 else 1
        if page > total_pages:
            page = total_pages
        schedules = (
            schedules_base_query
            .options(joinedload(Schedule.device))
            .order_by(Device.name.asc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )
        start_idx = ((page - 1) * per_page) + 1 if schedule_total > 0 else 0
        end_idx = min(page * per_page, schedule_total)

    active_devices = db.query(Device).filter(
        Device.tenant_id == tenant.id,
        Device.is_active == True
    ).options(
        joinedload(Device.group)
    ).all()

    active_groups = db.query(DeviceGroup).filter(
        DeviceGroup.tenant_id == tenant.id,
        DeviceGroup.is_active == True
    ).order_by(DeviceGroup.name.asc()).all()

    active_schedule_rows = (
        db.query(Schedule.device_id, Schedule.frequency, Schedule.time)
        .join(Device)
        .filter(
            Device.tenant_id == tenant.id,
            Schedule.is_active == True,
        )
        .all()
    )

    active_schedule_device_ids = {row.device_id for row in active_schedule_rows}

    with_schedule = 0
    without_schedule = 0
    group_overview_map = {}
    for group in active_groups:
        group_overview_map[str(group.id)] = {
            "group_id": str(group.id),
            "name": group.name,
            "connection_type": group.connection_type or "direct",
            "total": 0,
            "with_schedule": 0,
            "without_schedule": 0,
            "auto_disabled": 0,
            "coverage_pct": 0,
        }

    group_overview_map["__ungrouped__"] = {
        "group_id": None,
        "name": "Sem grupo",
        "connection_type": "direct",
        "total": 0,
        "with_schedule": 0,
        "without_schedule": 0,
        "auto_disabled": 0,
        "coverage_pct": 0,
    }

    for device in active_devices:
        has_active_schedule = device.id in active_schedule_device_ids
        fully_configured = device.backup_scheduled and has_active_schedule
        if fully_configured:
            with_schedule += 1
        else:
            without_schedule += 1

        if device.group_id:
            group_key = str(device.group_id)
            if group_key not in group_overview_map:
                group_overview_map[group_key] = {
                    "group_id": str(device.group_id),
                    "name": (device.group.name if device.group else "Grupo removido"),
                    "connection_type": (device.group.connection_type if device.group else "direct"),
                    "total": 0,
                    "with_schedule": 0,
                    "without_schedule": 0,
                    "auto_disabled": 0,
                    "coverage_pct": 0,
                }
        else:
            group_key = "__ungrouped__"

        bucket = group_overview_map[group_key]
        bucket["total"] += 1
        if fully_configured:
            bucket["with_schedule"] += 1
        else:
            bucket["without_schedule"] += 1
        if not device.backup_scheduled:
            bucket["auto_disabled"] += 1

    daily_times = []
    for row in active_schedule_rows:
        frequency_val = row.frequency.value if hasattr(row.frequency, "value") else str(row.frequency)
        if frequency_val == "daily" and row.time:
            daily_times.append(row.time)

    default_daily_time = "02:00"
    if daily_times:
        default_daily_time = Counter(daily_times).most_common(1)[0][0]

    schedule_overview = {
        "total_active_devices": len(active_devices),
        "with_schedule": with_schedule,
        "without_schedule": without_schedule,
        "default_daily_time": default_daily_time,
    }

    schedule_stats = {
        "total": schedule_total,
        "active": schedule_active,
        "paused": schedule_paused,
    }

    group_overview = []
    for group_data in group_overview_map.values():
        if group_data["total"] == 0:
            continue
        group_data["coverage_pct"] = int(round((group_data["with_schedule"] / group_data["total"]) * 100))
        group_overview.append(group_data)
    group_overview.sort(key=lambda g: (g["name"] == "Sem grupo", g["name"].lower()))

    missing_auto_query = (
        db.query(Device)
        .options(joinedload(Device.group))
        .filter(
            Device.tenant_id == tenant.id,
            Device.is_active == True,
            Device.backup_scheduled == False
        )
        .order_by(Device.name.asc())
    )
    missing_auto_total = missing_auto_query.count()
    missing_auto_total_pages = (missing_auto_total + missing_per_page - 1) // missing_per_page if missing_auto_total > 0 else 1
    if missing_page > missing_auto_total_pages:
        missing_page = missing_auto_total_pages
    missing_auto_devices = missing_auto_query.offset((missing_page - 1) * missing_per_page).limit(missing_per_page).all()
    missing_auto_start = ((missing_page - 1) * missing_per_page) + 1 if missing_auto_total > 0 else 0
    missing_auto_end = min(missing_page * missing_per_page, missing_auto_total)
        
    db.close()
    return render_template(
        'tenant/schedules/list.html',
        tenant=tenant,
        schedules=schedules,
        schedule_overview=schedule_overview,
        schedule_stats=schedule_stats,
        show_details=show_details,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        start_idx=start_idx,
        end_idx=end_idx,
        group_overview=group_overview,
        missing_auto_devices=missing_auto_devices,
        missing_auto_total=missing_auto_total,
        missing_auto_page=missing_page,
        missing_auto_per_page=missing_per_page,
        missing_auto_total_pages=missing_auto_total_pages,
        missing_auto_start=missing_auto_start,
        missing_auto_end=missing_auto_end,
    )


@bp.route('/apply-daily', methods=['POST'])
@login_required
@tenant_admin_required
def apply_daily_schedule(tenant_slug):
    db, tenant = get_db_and_tenant(tenant_slug)
    if not tenant:
        db.close()
        return "Tenant not found", 404

    time_str = (request.form.get('daily_time') or '').strip()
    apply_scope = (request.form.get('apply_scope') or 'missing').strip()
    return_to = (request.form.get('return_to') or '').strip()
    if apply_scope not in {'missing', 'all'}:
        apply_scope = 'missing'

    if not _is_valid_time(time_str):
        flash('Horario invalido. Use o formato HH:MM.', 'error')
        db.close()
        return redirect(_resolve_return_url(tenant_slug, return_to))

    devices = db.query(Device).filter(
        Device.tenant_id == tenant.id,
        Device.is_active == True
    ).all()

    if not devices:
        flash('Nenhum dispositivo ativo encontrado para aplicar agendamento.', 'warning')
        db.close()
        return redirect(_resolve_return_url(tenant_slug, return_to))

    affected = _apply_daily_to_devices(db, devices, time_str, apply_scope)

    if affected == 0:
        flash('Nenhum dispositivo precisa de ajuste no modo selecionado.', 'info')
        db.close()
        return redirect(_resolve_return_url(tenant_slug, return_to))

    user_id = session.get('user_id')
    ActivityService.log_action(
        db,
        tenant.id,
        user_id,
        "BULK_SCHEDULE_UPDATE",
        f"Aplicado agendamento diario {time_str} para {affected} dispositivos (modo={apply_scope}).",
        request.remote_addr,
    )

    db.commit()
    db.close()

    if apply_scope == 'missing':
        flash(f'Agendamento diario {time_str} aplicado para {affected} dispositivos sem rotina ativa.', 'success')
    else:
        flash(f'Agendamento diario {time_str} aplicado para {affected} dispositivos ativos.', 'success')
    return redirect(_resolve_return_url(tenant_slug, return_to))


@bp.route('/apply-daily-group', methods=['POST'])
@login_required
@tenant_admin_required
def apply_daily_schedule_for_group(tenant_slug):
    db, tenant = get_db_and_tenant(tenant_slug)
    if not tenant:
        db.close()
        return "Tenant not found", 404

    group_id_raw = (request.form.get('group_id') or '').strip()
    time_str = (request.form.get('daily_time') or '').strip()
    apply_scope = (request.form.get('apply_scope') or 'missing').strip()
    return_to = (request.form.get('return_to') or '').strip()
    if apply_scope not in {'missing', 'all'}:
        apply_scope = 'missing'

    if not _is_valid_time(time_str):
        flash('Horario invalido. Use o formato HH:MM.', 'error')
        db.close()
        return redirect(_resolve_return_url(tenant_slug, return_to))

    if not group_id_raw:
        flash('Grupo invalido para aplicar agendamento.', 'error')
        db.close()
        return redirect(_resolve_return_url(tenant_slug, return_to))

    try:
        group_uuid = uuid.UUID(group_id_raw)
    except ValueError:
        flash('ID de grupo invalido.', 'error')
        db.close()
        return redirect(_resolve_return_url(tenant_slug, return_to))

    group = db.query(DeviceGroup).filter(
        DeviceGroup.id == group_uuid,
        DeviceGroup.tenant_id == tenant.id,
        DeviceGroup.is_active == True
    ).first()

    if not group:
        flash('Grupo nao encontrado para este tenant.', 'error')
        db.close()
        return redirect(_resolve_return_url(tenant_slug, return_to))

    devices = db.query(Device).filter(
        Device.tenant_id == tenant.id,
        Device.group_id == group.id,
        Device.is_active == True
    ).all()

    if not devices:
        flash(f'Grupo {group.name} nao possui dispositivos ativos.', 'warning')
        db.close()
        return redirect(_resolve_return_url(tenant_slug, return_to))

    affected = _apply_daily_to_devices(db, devices, time_str, apply_scope)

    if affected == 0:
        flash(f'Nenhum dispositivo do grupo {group.name} precisa de ajuste.', 'info')
        db.close()
        return redirect(_resolve_return_url(tenant_slug, return_to))

    user_id = session.get('user_id')
    ActivityService.log_action(
        db,
        tenant.id,
        user_id,
        "GROUP_SCHEDULE_UPDATE",
        f"Aplicado agendamento diario {time_str} no grupo {group.name} para {affected} dispositivos (modo={apply_scope}).",
        request.remote_addr,
    )

    db.commit()
    db.close()

    if apply_scope == 'missing':
        flash(f'Grupo {group.name}: agendamento {time_str} aplicado para {affected} dispositivos pendentes.', 'success')
    else:
        flash(f'Grupo {group.name}: agendamento {time_str} aplicado para {affected} dispositivos.', 'success')
    return redirect(_resolve_return_url(tenant_slug, return_to))



def _is_backup_task_name(task_name: str) -> bool:
    name = str(task_name or '')
    if name.startswith('app.tasks.backups.'):
        return True
    # Inclui testes de conexao em massa para que "Parar tudo" atue neles tambem.
    if name.startswith('app.tasks.monitoring.run_device_connection_audit_task'):
        return True
    if name.startswith('app.tasks.monitoring.run_connection_test_task'):
        return True
    return False


def _stop_backup_tasks_globally() -> dict:
    """
    Interrompe tasks de backup ativas/pendentes e marca lotes bulk em aberto como interrompidos.
    """
    inspect = celery_app.control.inspect(timeout=1.0)
    matched = 0
    revoked = 0

    for getter_name in ('active', 'reserved', 'scheduled'):
        getter = getattr(inspect, getter_name, None)
        if not getter:
            continue
        data = getter() or {}
        for _worker, tasks in data.items():
            for item in (tasks or []):
                task_name = item.get('name') if isinstance(item, dict) else None
                req = item.get('request') if isinstance(item, dict) else None
                if not task_name and isinstance(req, dict):
                    task_name = req.get('name')
                if not _is_backup_task_name(task_name):
                    continue
                task_id = item.get('id') if isinstance(item, dict) else None
                if not task_id and isinstance(req, dict):
                    task_id = req.get('id')
                if not task_id:
                    continue
                matched += 1
                try:
                    is_running = getter_name == 'active'
                    is_vpn_group_task = str(task_name or '').endswith('run_vpn_group_backups_task')
                    # Evita SIGKILL/SIGTERM em task VPN ativa (nmcli em network_mode host),
                    # reduzindo risco de derrubar conectividade da VM.
                    if is_running and not is_vpn_group_task:
                        celery_app.control.revoke(str(task_id), terminate=True, signal='SIGTERM')
                    else:
                        celery_app.control.revoke(str(task_id))
                    revoked += 1
                except Exception:
                    pass

    queue_removed = 0
    bulk_marked = 0
    r = get_redis_client()
    if r:
        # Trava global temporaria para impedir novas execucoes enquanto o operador estabiliza o ambiente.
        try:
            r.setex('backup_center:force_stop_backups', 60 * 3, '1')
        except Exception:
            pass

        for queue_name in ('celery', 'vpn_queue'):
            try:
                queue_len = int(r.llen(queue_name) or 0)
                if queue_len > 0:
                    r.delete(queue_name)
                    queue_removed += queue_len
            except Exception:
                pass

        try:
            for key in r.scan_iter('backup_center:task_meta:*'):
                raw = r.get(key)
                if not raw:
                    continue
                try:
                    meta = json.loads(raw)
                except Exception:
                    continue
                if meta.get('is_bulk') and not meta.get('completed'):
                    meta['cancel_requested'] = True
                    meta['status'] = 'stopped'
                    meta['completed'] = True
                    meta['progress'] = 100
                    meta['message'] = 'Lote interrompido manualmente pela central de agendamentos.'
                    r.setex(key, 60 * 60 * 48, json.dumps(meta, ensure_ascii=False))
                    bulk_marked += 1
        except Exception:
            pass

    return {
        'matched_runtime_tasks': matched,
        'revoked_runtime_tasks': revoked,
        'removed_queued_tasks': queue_removed,
        'bulk_marked_stopped': bulk_marked,
    }


@bp.route('/stop-all-backups', methods=['POST'])
@login_required
@tenant_admin_required
def stop_all_backups(tenant_slug):
    db, tenant = get_db_and_tenant(tenant_slug)
    if not tenant:
        db.close()
        return 'Tenant not found', 404

    stats = _stop_backup_tasks_globally()

    user_id = session.get('user_id')
    details = (
        f"Stop all backups requested: revoked={stats['revoked_runtime_tasks']} "
        f"queued_removed={stats['removed_queued_tasks']} bulk_marked={stats['bulk_marked_stopped']}"
    )
    ActivityService.log_action(db, tenant.id, user_id, 'BACKUP_STOP_ALL', details, request.remote_addr)
    db.close()

    is_ajax = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in (request.headers.get('Accept') or '')
    )
    if is_ajax:
        return jsonify({'ok': True, **stats})

    flash(
        'Parada global executada. '
        f"Revogadas: {stats['revoked_runtime_tasks']} | "
        f"Fila removida: {stats['removed_queued_tasks']} | "
        f"Lotes finalizados: {stats['bulk_marked_stopped']}.",
        'warning',
    )
    return redirect(url_for('tenant_schedules.list_schedules', tenant_slug=tenant_slug))
