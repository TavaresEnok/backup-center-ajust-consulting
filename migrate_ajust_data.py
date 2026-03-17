"""
Script de Migração: Sistema Legado Ajust Consulting -> SaaS Multi-tenant

Este script migra todos os dados do dump MySQL do sistema antigo para o novo sistema.
Inclui: Usuários, Tipos de Equipamento, Provedores (como Grupos), Dispositivos e Logs.
"""

import os
import re
import sys
import uuid
from datetime import datetime

# Adiciona o diretório raiz ao path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from werkzeug.security import generate_password_hash
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.database import Base, engine, SessionLocal
from app.models import (
    Tenant, User, UserRole, Device, DeviceType, DeviceGroup,
    Plan, Backup, BackupStatus
)

# ============================================================================
# CONFIGURAÇÕES
# ============================================================================

# FERNET KEY do sistema antigo (para descriptografar senhas de dispositivos)
LEGACY_FERNET_KEY = 'QNfC_3GDRMkG8NN8Pw3fPbK1qhYBCoItYgEaXEEUZCU='
legacy_fernet = Fernet(LEGACY_FERNET_KEY.encode())

# Caminho do dump SQL
SQL_DUMP_PATH = 'antigos/db_backup_2026-01-13_02-30-01.sql'

# Diretório de backups do sistema antigo
LEGACY_BACKUP_DIR = '/srv/mikrotik_manager/backups'

# Mapeamento de usuários especiais
SUPER_ADMINS = ['enok@ajustconsulting.com.br', 'arthur@ajustconsulting.com.br', 'cleyton@ajustconsulting.com.br']
TENANT_OWNERS = ['audemario@ajustconsulting.com.br', 'enok@ajustconsulting.com.br', 'arthur@ajustconsulting.com.br', 'cleyton@ajustconsulting.com.br']

# Senha customizada para Enok
ENOK_NEW_PASSWORD = 'asdSD@91582685'

# ============================================================================
# PARSERS PARA DUMP SQL
# ============================================================================

def parse_sql_insert(sql_content, table_name):
    """Extrai dados de INSERT statements de um dump SQL."""
    pattern = f"INSERT INTO `{table_name}` VALUES\\s*"
    match = re.search(pattern, sql_content, re.IGNORECASE)
    if not match:
        return []
    
    start_pos = match.end()
    # Encontra o final do statement (;)
    depth = 0
    in_string = False
    escape_next = False
    end_pos = start_pos
    
    for i, char in enumerate(sql_content[start_pos:]):
        if escape_next:
            escape_next = False
            continue
        if char == '\\':
            escape_next = True
            continue
        if char == "'" and not in_string:
            in_string = True
        elif char == "'" and in_string:
            in_string = False
        elif char == '(' and not in_string:
            depth += 1
        elif char == ')' and not in_string:
            depth -= 1
        elif char == ';' and not in_string and depth == 0:
            end_pos = start_pos + i
            break
    
    values_str = sql_content[start_pos:end_pos]
    
    # Parse individual rows
    rows = []
    current_row = []
    current_value = ""
    in_string = False
    escape_next = False
    depth = 0
    
    for char in values_str:
        if escape_next:
            current_value += char
            escape_next = False
            continue
        if char == '\\':
            escape_next = True
            current_value += char
            continue
            
        if char == "'" and not in_string:
            in_string = True
        elif char == "'" and in_string:
            in_string = False
        elif char == '(' and not in_string:
            depth += 1
            if depth == 1:
                current_row = []
                current_value = ""
                continue
        elif char == ')' and not in_string:
            depth -= 1
            if depth == 0:
                if current_value or current_row:
                    current_row.append(parse_value(current_value.strip()))
                rows.append(tuple(current_row))
                continue
        elif char == ',' and not in_string and depth == 1:
            current_row.append(parse_value(current_value.strip()))
            current_value = ""
            continue
        
        if depth >= 1:
            current_value += char
    
    return rows


def parse_value(val):
    """Converte um valor SQL para Python."""
    if val == 'NULL':
        return None
    if val.startswith("'") and val.endswith("'"):
        # Remove quotes e unescape
        return val[1:-1].replace("\\'", "'").replace("\\\\", "\\")
    if val.isdigit():
        return int(val)
    try:
        return float(val)
    except:
        return val


def decrypt_legacy_password(encrypted_blob):
    """Descriptografa senha do sistema antigo usando Fernet."""
    if not encrypted_blob:
        return None
    try:
        # O blob vem como string do dump SQL
        if isinstance(encrypted_blob, str):
            decrypted = legacy_fernet.decrypt(encrypted_blob.encode())
        else:
            decrypted = legacy_fernet.decrypt(encrypted_blob)
        return decrypted.decode()
    except Exception as e:
        print(f"Erro ao descriptografar: {e}")
        return None


# ============================================================================
# MIGRAÇÃO
# ============================================================================

def create_tables():
    """Cria todas as tabelas no banco de dados."""
    print("Criando tabelas...")
    Base.metadata.create_all(bind=engine)
    print("Tabelas criadas com sucesso!")


def migrate_device_types(db, sql_content):
    """Migra tipos de equipamento (global)."""
    print("\n[1/6] Migrando Tipos de Equipamento...")
    
    rows = parse_sql_insert(sql_content, 'tipo_equipamento')
    count = 0
    
    # Mapeamento de categorias
    category_map = {
        'OLT': 'olt',
        'Switch': 'switch',
        'Router': 'router',
        'Firewall': 'firewall',
        'Zabbix': 'server',
        'Grafana': 'server',
        'ERP': 'erp',
        'MikroTik': 'router',
        'CGNat': 'cgnat',
    }
    
    for row in rows:
        legacy_id, name, script_name, required_params = row[:4]
        
        # Verifica se ja existe
        existing = db.query(DeviceType).filter_by(name=name).first()
        if existing:
            print(f"  [SKIP] Tipo {name} ja existe")
            continue
        
        # Determina categoria
        category = 'other'
        for key, cat in category_map.items():
            if key.lower() in name.lower():
                category = cat
                break
        
        # Cria slug unico
        slug = name.lower().replace(' ', '_').replace('-', '_')
        slug = re.sub(r'[^a-z0-9_]', '', slug)
        slug = f"{slug}_{legacy_id}"  # Garante unicidade
        
        device_type = DeviceType(
            name=name,
            slug=slug,
            script_name=script_name,
            required_parameters=required_params,
            category=category,
            is_active=True
        )
        db.add(device_type)
        count += 1
    
    db.commit()
    print(f"  [OK] {count} tipos de equipamento migrados")
    return {row[0]: row[1] for row in rows}  # legacy_id -> name


def get_or_create_enterprise_plan(db):
    """Obtém ou cria o plano Enterprise."""
    plan = db.query(Plan).filter_by(slug='enterprise').first()
    if not plan:
        plan = Plan(
            name='Enterprise',
            slug='enterprise',
            description='Plano completo para grandes operações',
            price_monthly=99900,  # R$ 999,00
            price_yearly=999900,  # R$ 9.999,00
            max_devices=9999,
            max_users=100,
            backup_retention_days=365,
            features={'vpn': True, 'api': True, 'priority_support': True},
            is_active=True
        )
        db.add(plan)
        db.commit()
    return plan


def create_ajust_tenant(db, plan):
    """Cria o tenant Ajust Consulting."""
    print("\n[2/6] Criando Tenant Ajust Consulting...")
    
    tenant = db.query(Tenant).filter_by(slug='ajust-consulting').first()
    if tenant:
        print("  [WARN] Tenant ja existe, pulando...")
        return tenant
    
    tenant = Tenant(
        name='Ajust Consulting',
        slug='ajust-consulting',
        company_name='Ajust Consulting Tecnologia LTDA',
        cnpj='XX.XXX.XXX/0001-XX',
        email='contato@ajustconsulting.com.br',
        phone='(81) 99999-9999',
        is_active=True,
        subscription_status='active',
        plan_id=plan.id
    )
    db.add(tenant)
    db.commit()
    print(f"  [OK] Tenant criado: {tenant.name} (ID: {tenant.id})")
    return tenant


def migrate_users(db, sql_content, tenant):
    """Migra usuários do sistema antigo."""
    print("\n[3/6] Migrando Usuários...")
    
    rows = parse_sql_insert(sql_content, 'user')
    count_super = 0
    count_owner = 0
    count_admin = 0
    count_tech = 0
    
    for row in rows:
        legacy_id, username, password_hash, is_admin = row[:4]
        
        # Normaliza email
        email = username if '@' in username else f"{username}@ajustconsulting.com.br"
        
        # Verifica se já existe
        existing = db.query(User).filter_by(email=email).first()
        if existing:
            print(f"  [SKIP] Usuario {email} ja existe")
            continue
        
        # Determina role
        if email in SUPER_ADMINS:
            role = UserRole.SUPER_ADMIN
            tenant_id = None
            count_super += 1
        elif email in TENANT_OWNERS:
            role = UserRole.TENANT_OWNER
            tenant_id = tenant.id
            count_owner += 1
        elif is_admin == 1:
            role = UserRole.TENANT_ADMIN
            tenant_id = tenant.id
            count_admin += 1
        else:
            role = UserRole.TENANT_TECHNICIAN
            tenant_id = tenant.id
            count_tech += 1
        
        # Determina senha
        if email == 'enok@ajustconsulting.com.br':
            final_password = generate_password_hash(ENOK_NEW_PASSWORD)
        else:
            final_password = password_hash  # Mantém hash bcrypt original
        
        # Extrai nome do email
        name_part = email.split('@')[0].replace('.', ' ').replace('_', ' ').title()
        
        user = User(
            email=email,
            full_name=name_part,
            password_hash=final_password,
            role=role,
            tenant_id=tenant_id,
            is_active=True
        )
        db.add(user)
    
    db.commit()
    print(f"  [OK] Super Admins: {count_super}")
    print(f"  [OK] Tenant Owners: {count_owner}")
    print(f"  [OK] Tenant Admins: {count_admin}")
    print(f"  [OK] Tecnicos: {count_tech}")


def migrate_device_groups(db, sql_content, tenant):
    """Migra provedores como grupos de dispositivos."""
    print("\n[4/6] Migrando Provedores -> Grupos...")
    
    rows = parse_sql_insert(sql_content, 'provedor')
    count = 0
    group_map = {}  # legacy_id -> new_group_id
    
    for row in rows:
        legacy_id = row[0]
        nome = row[1]
        usa_vpn = row[2] == 1
        vpn_tipo = row[3]
        vpn_servidor = row[4]
        vpn_usuario = row[5]
        vpn_senha_blob = row[6]
        vpn_ipsec_blob = row[7]
        
        # Cria slug
        slug = nome.lower().replace(' ', '-').replace('_', '-')
        slug = re.sub(r'[^a-z0-9-]', '', slug)
        
        # Descriptografa senhas VPN se necessário
        vpn_senha = None
        vpn_ipsec = None
        if usa_vpn and vpn_senha_blob:
            vpn_senha = decrypt_legacy_password(vpn_senha_blob)
        if usa_vpn and vpn_ipsec_blob:
            vpn_ipsec = decrypt_legacy_password(vpn_ipsec_blob)
        
        group = DeviceGroup(
            tenant_id=tenant.id,
            name=nome,
            slug=slug,
            uses_vpn=usa_vpn,
            vpn_type=vpn_tipo or 'l2tp',
            vpn_server=vpn_servidor,
            vpn_username=vpn_usuario,
            vpn_password_encrypted=vpn_senha,  # Será re-criptografado pelo sistema
            vpn_ipsec_secret_encrypted=vpn_ipsec,
            is_active=True
        )
        db.add(group)
        db.flush()  # Para obter o ID
        group_map[legacy_id] = group.id
        count += 1
    
    db.commit()
    print(f"  [OK] {count} grupos criados")
    return group_map


def migrate_devices(db, sql_content, tenant, group_map, type_map):
    """Migra dispositivos."""
    print("\n[5/6] Migrando Dispositivos...")
    
    rows = parse_sql_insert(sql_content, 'dispositivo')
    count = 0
    
    # Busca tipos de equipamento do novo sistema
    device_types = {dt.name: dt.id for dt in db.query(DeviceType).all()}
    
    # Busca parâmetros de dispositivos
    param_rows = parse_sql_insert(sql_content, 'parametro_dispositivo')
    params_by_device = {}
    for row in param_rows:
        device_id = row[3]  # dispositivo_id
        param_name = row[1]  # nome
        param_value_blob = row[2]  # valor (encrypted)
        
        if device_id not in params_by_device:
            params_by_device[device_id] = {}
        
        # Descriptografa o valor
        decrypted_value = decrypt_legacy_password(param_value_blob)
        params_by_device[device_id][param_name] = decrypted_value
    
    for row in rows:
        legacy_id = row[0]
        nome = row[1]
        ip = row[2]
        usuario = row[3]
        porta = row[4]
        backup_agendado = row[5] == 1
        provedor_id = row[6]
        tipo_id = row[7]
        is_legacy = row[8] == 1 if len(row) > 8 else False
        use_telnet = row[9] == 1 if len(row) > 9 else False
        is_vpn_gateway = row[10] == 1 if len(row) > 10 else False
        
        # Busca tipo de equipamento original
        tipo_nome = type_map.get(tipo_id)
        device_type_id = device_types.get(tipo_nome) if tipo_nome else None
        
        # Busca grupo
        group_id = group_map.get(provedor_id)
        
        # Busca parâmetros (incluindo senha)
        extra_params = params_by_device.get(legacy_id, {})
        password = extra_params.pop('password', 'MIGRAR_SENHA')
        
        device = Device(
            tenant_id=tenant.id,
            group_id=group_id,
            device_type_id=device_type_id,
            legacy_id=legacy_id,
            name=nome,
            ip_address=ip,
            port=porta,
            username=usuario,
            password_encrypted=password,  # Será re-criptografado
            use_telnet=use_telnet,
            is_vpn_gateway=is_vpn_gateway,
            backup_scheduled=backup_agendado,
            extra_parameters=extra_params,
            is_active=True
        )
        db.add(device)
        count += 1
    
    db.commit()
    print(f"  [OK] {count} dispositivos migrados")


def print_summary():
    """Imprime resumo da migração."""
    print("\n" + "="*60)
    print("MIGRAÇÃO CONCLUÍDA COM SUCESSO!")
    print("="*60)
    print("\nPróximos passos:")
    print("1. Execute 'python update_db_schema.py' para atualizar o banco")
    print("2. Reinicie o servidor: python start_server.py")
    print("3. Acesse: http://localhost:8000/auth/login")
    print("4. Login como Super Admin: enok@ajustconsulting.com.br")
    print("="*60)


def main():
    """Função principal de migração."""
    print("="*60)
    print("MIGRAÇÃO: Sistema Ajust Consulting -> SaaS Multi-tenant")
    print("="*60)
    
    # Lê o dump SQL
    print(f"\nLendo dump SQL: {SQL_DUMP_PATH}")
    with open(SQL_DUMP_PATH, 'r', encoding='utf-8') as f:
        sql_content = f.read()
    print(f"  [OK] {len(sql_content):,} bytes lidos")
    
    # Cria sessão
    db = SessionLocal()
    
    try:
        # 0. Cria tabelas
        create_tables()
        
        # 1. Migra tipos de equipamento
        type_map = migrate_device_types(db, sql_content)
        
        # 2. Cria plano e tenant
        plan = get_or_create_enterprise_plan(db)
        tenant = create_ajust_tenant(db, plan)
        
        # 3. Migra usuários
        migrate_users(db, sql_content, tenant)
        
        # 4. Migra provedores -> grupos
        group_map = migrate_device_groups(db, sql_content, tenant)
        
        # 5. Migra dispositivos
        migrate_devices(db, sql_content, tenant, group_map, type_map)
        
        # 6. Resumo
        print_summary()
        
    except Exception as e:
        print(f"\n[ERROR] ERRO: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == '__main__':
    main()
