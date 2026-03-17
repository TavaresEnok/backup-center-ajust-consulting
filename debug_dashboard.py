import sys
import os
sys.path.append('/app')

from app.core.database import SessionLocal
from app.models.device import Device
from app.models.backup import Backup, BackupStatus
from app.models.tenant import Tenant
from sqlalchemy import func

print("Starting diagnostics...")
db = SessionLocal()
try:
    tenant_slug = 'ajust-consulting'
    print(f"Querying tenant: {tenant_slug}")
    tenant = db.query(Tenant).filter(Tenant.slug == tenant_slug).first()
    
    if not tenant:
        print("ERROR: Tenant not found!")
        sys.exit(1)
    
    print(f"Tenant found: {tenant.id} - {tenant.name}")
    
    print("Querying total_devices...")
    total_devices = db.query(Device).filter(Device.tenant_id == tenant.id).count()
    print(f"Total devices: {total_devices}")
    
    print("Querying storage stats...")
    # Esta query tem joins complexos
    storage = db.query(func.sum(Backup.file_size_bytes)).join(Device).filter(Device.tenant_id == tenant.id).scalar()
    print(f"Storage: {storage}")
    
    print("Querying recent backups...")
    recent = db.query(Backup).join(Device).filter(Device.tenant_id == tenant.id).limit(5).all()
    print(f"Recent backups: {len(recent)}")

except Exception as e:
    print(f"\nCRITICAL ERROR: {e}")
    import traceback
    traceback.print_exc()
finally:
    db.close()
