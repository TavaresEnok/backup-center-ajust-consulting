"""
Script para atualizar o schema do banco de dados PostgreSQL.
Adiciona as novas tabelas e colunas necessárias para a migração.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app.core.database import engine, SessionLocal, Base
from app.models import *  # Importa todos os models

def update_schema():
    """Atualiza o schema do banco de dados."""
    print("Atualizando schema do banco de dados...")
    
    # Cria todas as tabelas que não existem
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    
    try:
        # Verifica e adiciona colunas faltantes na tabela devices
        columns_to_add = [
            ("devices", "group_id", "UUID REFERENCES device_groups(id)"),
            ("devices", "device_type_id", "UUID REFERENCES device_types(id)"),
            ("devices", "legacy_id", "INTEGER"),
            ("devices", "use_telnet", "BOOLEAN DEFAULT FALSE"),
            ("devices", "is_vpn_gateway", "BOOLEAN DEFAULT FALSE"),
            ("devices", "backup_scheduled", "BOOLEAN DEFAULT FALSE"),
            ("devices", "extra_parameters", "JSONB DEFAULT '{}'"),
            ("devices", "last_backup_status", "VARCHAR(20) DEFAULT 'never'"),
        ]
        
        for table, column, col_type in columns_to_add:
            try:
                db.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}"))
                print(f"  ✓ Coluna {table}.{column} verificada/adicionada")
            except Exception as e:
                if 'already exists' not in str(e).lower():
                    print(f"  ⚠ Aviso para {table}.{column}: {e}")
        
        db.commit()
        print("\nSchema atualizado com sucesso!")
        
    except Exception as e:
        print(f"Erro: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == '__main__':
    update_schema()
