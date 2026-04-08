from app.core.database import SessionLocal
from app.models import Device, DeviceGroup, Backup
from app.services.connection_mode import get_effective_connection_type

db=SessionLocal()
try:
    g=db.query(DeviceGroup).filter(DeviceGroup.name=='FlashNet').first()
    if not g:
        print('GROUP_NOT_FOUND')
        raise SystemExit(0)
    devices=(db.query(Device)
             .filter(Device.group_id==g.id)
             .filter(Device.is_active==True)
             .order_by(Device.name.asc())
             .all())
    for d in devices:
        b=(db.query(Backup).filter(Backup.device_id==d.id).order_by(Backup.created_at.desc()).first())
        if not b:
            continue
        st=str(b.status)
        if st.endswith('FAILED'):
            mode=get_effective_connection_type(d.group, device=d) if d.group else 'direct'
            print(f"{d.id}|{d.name}|mode={mode}|err={(b.error_message or '')[:220]}")
finally:
    db.close()
