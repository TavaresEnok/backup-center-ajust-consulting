import sys
sys.path.insert(0, '/app')

try:
    from app.core.database import SessionLocal
    from app.models.device import Device

    db = SessionLocal()
    # Search for CELLNET device
    devices = db.query(Device).filter(Device.name.ilike('%CELLNET%')).all()
    if not devices:
        # Try broader search
        devices = db.query(Device).filter(Device.ip_address.like('190.89.232.%')).limit(20).all()
    
    for d in devices:
        print(f"ID: {d.id}")
        print(f"Nome: {d.name}")
        print(f"IP no DB: {d.ip_address}")
        print(f"Grupo ID: {d.group_id}")
        print("---")
    
    if not devices:
        print("Nenhum dispositivo encontrado com CELLNET ou 190.89.232.*")
        # List all devices and IPs
        all_devs = db.query(Device.name, Device.ip_address).filter(Device.is_active.isnot(False)).limit(30).all()
        for name, ip in all_devs:
            print(f"  {name}: {ip}")
    db.close()
except Exception as e:
    print(f"ERRO: {e}")
    import traceback
    traceback.print_exc()
