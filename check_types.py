from app.core.database import SessionLocal
from app.models.device_type import DeviceType
from sqlalchemy import text

db = SessionLocal()
try:
    print("Checking DeviceType table...")
    types = db.query(DeviceType).all()
    for t in types:
        print(f"Type: {t.name}, Slug: {t.slug}")
        
    print("Checking specific slugs...")
    mkt = db.query(DeviceType.id).filter(DeviceType.slug == 'mikrotik').first()
    print(f"Mikrotik ID: {mkt}")

except Exception as e:
    print(f"ERROR: {e}")
finally:
    db.close()
