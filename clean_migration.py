"""
Limpa os dados migrados anteriormente para permitir nova migração.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app.core.database import SessionLocal

def clean_migration_data():
    """Remove dados da migração anterior."""
    db = SessionLocal()
    
    try:
        print("Limpando dados de migracoes anteriores...")
        
        # Ordem importante por causa das foreign keys
        tables = [
            'backups',
            'schedules',
            'devices',
            'device_groups', 
            'device_types',
            'notifications',
            'subscriptions',
            'invoices',
            'users',
            'tenants',
        ]
        
        for table in tables:
            try:
                result = db.execute(text(f"DELETE FROM {table}"))
                print(f"  [OK] {table}: {result.rowcount} registros removidos")
            except Exception as e:
                print(f"  [WARN] {table}: {e}")
        
        db.commit()
        print("\nLimpeza concluida!")
        
    except Exception as e:
        print(f"Erro: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == '__main__':
    clean_migration_data()
