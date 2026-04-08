from app.core.database import SessionLocal
from app.models import Device, DeviceGroup, Backup
from sqlalchemy import and_


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
    print('total_active',len(devices))
    failed=0
    success=0
    unknown=0
    for d in devices:
        b=(db.query(Backup).filter(Backup.device_id==d.id).order_by(Backup.created_at.desc()).first())
        st=str(b.status) if b else 'NONE'
        if st.endswith('SUCCESS'):
            success+=1
        elif st.endswith('FAILED'):
            failed+=1
        else:
            unknown+=1
    print('last_status_success',success)
    print('last_status_failed',failed)
    print('last_status_unknown',unknown)
finally:
    db.close()
