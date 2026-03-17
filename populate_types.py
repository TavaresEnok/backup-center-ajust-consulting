from app.core.database import SessionLocal
from app.models.device_type import DeviceType
import uuid

def populate():
    db = SessionLocal()
    
    types = [
        {
            "name": "MikroTik RouterOS",
            "slug": "mikrotik",
            "script_name": "mikrotik_backup.py",
            "description": "Roteadores MikroTik",
            "category": "router",
            "default_port": 22,
            "use_telnet": False
        },
        {
            "name": "Huawei OLT",
            "slug": "huawei-olt",
            "script_name": "huawei_olt_backup.py",
            "description": "OLTs Huawei",
            "category": "olt",
            "default_port": 22,
            "use_telnet": False
        },
        {
            "name": "Datacom Switch",
            "slug": "switch",
            "script_name": "datacom_backup.py",
            "description": "Switches Datacom/Genericos",
            "category": "switch",
            "default_port": 22,
            "use_telnet": False
        }
    ]
    
    for t in types:
        existing = db.query(DeviceType).filter_by(slug=t['slug']).first()
        if not existing:
            print(f"Adding {t['name']}...")
            new_type = DeviceType(**t)
            db.add(new_type)
        else:
            print(f"Skipping {t['name']} (already exists)")
            
    try:
        db.commit()
        print("Done!")
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    populate()
