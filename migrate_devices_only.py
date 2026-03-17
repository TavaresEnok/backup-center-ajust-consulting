"""
Migra apenas os dispositivos do sistema legado.
Já assume que tipos, grupos, usuários e tenant foram migrados.
"""
import os
import re
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from cryptography.fernet import Fernet
from app.core.database import SessionLocal
from app.models import Device, DeviceType, DeviceGroup, Tenant

# FERNET KEY do sistema antigo
LEGACY_FERNET_KEY = 'QNfC_3GDRMkG8NN8Pw3fPbK1qhYBCoItYgEaXEEUZCU='
legacy_fernet = Fernet(LEGACY_FERNET_KEY.encode())

SQL_DUMP_PATH = 'antigos/db_backup_2026-01-13_02-30-01.sql'


def parse_sql_insert(sql_content, table_name):
    """Extrai dados de INSERT statements de um dump SQL."""
    pattern = f"INSERT INTO `{table_name}` VALUES\\s*"
    match = re.search(pattern, sql_content, re.IGNORECASE)
    if not match:
        return []
    
    start_pos = match.end()
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
        if isinstance(encrypted_blob, str):
            decrypted = legacy_fernet.decrypt(encrypted_blob.encode())
        else:
            decrypted = legacy_fernet.decrypt(encrypted_blob)
        return decrypted.decode()
    except Exception as e:
        return None


def main():
    print("Migrando dispositivos...")
    
    # Lê SQL
    with open(SQL_DUMP_PATH, 'r', encoding='utf-8') as f:
        sql_content = f.read()
    
    db = SessionLocal()
    
    try:
        # Busca tenant
        tenant = db.query(Tenant).filter_by(slug='ajust-consulting').first()
        if not tenant:
            print("ERRO: Tenant Ajust Consulting nao encontrado!")
            return
        
        print(f"Tenant: {tenant.name} (ID: {tenant.id})")
        
        # Mapeia grupos por legacy_id usando nome
        provedor_rows = parse_sql_insert(sql_content, 'provedor')
        provedor_names = {row[0]: row[1] for row in provedor_rows}
        
        groups = db.query(DeviceGroup).filter_by(tenant_id=tenant.id).all()
        group_map = {}
        for g in groups:
            for legacy_id, nome in provedor_names.items():
                if g.name == nome:
                    group_map[legacy_id] = g.id
                    break
        print(f"Grupos mapeados: {len(group_map)}")
        
        # Mapeia tipos por nome
        tipo_rows = parse_sql_insert(sql_content, 'tipo_equipamento')
        tipo_names = {row[0]: row[1] for row in tipo_rows}
        
        device_types = {dt.name: dt.id for dt in db.query(DeviceType).all()}
        print(f"Tipos de dispositivo: {len(device_types)}")
        
        # Busca parametros de dispositivos
        param_rows = parse_sql_insert(sql_content, 'parametro_dispositivo')
        params_by_device = {}
        for row in param_rows:
            device_id = row[3]
            param_name = row[1]
            param_value_blob = row[2]
            
            if device_id not in params_by_device:
                params_by_device[device_id] = {}
            
            decrypted_value = decrypt_legacy_password(param_value_blob)
            if decrypted_value:
                params_by_device[device_id][param_name] = decrypted_value
        
        print(f"Parametros: {len(params_by_device)} dispositivos com params")
        
        # Migra dispositivos
        device_rows = parse_sql_insert(sql_content, 'dispositivo')
        count = 0
        errors = 0
        
        for row in device_rows:
            try:
                legacy_id = row[0]
                nome = row[1]
                ip = row[2]
                usuario = row[3]
                porta = row[4]
                backup_agendado = row[5] == 1 if row[5] else False
                provedor_id = row[6]
                tipo_id = row[7]
                is_legacy = row[8] == 1 if len(row) > 8 and row[8] else False
                use_telnet = row[9] == 1 if len(row) > 9 and row[9] else False
                is_vpn_gateway = row[10] == 1 if len(row) > 10 and row[10] else False
                
                # Tipo
                tipo_nome = tipo_names.get(tipo_id)
                device_type_id = device_types.get(tipo_nome) if tipo_nome else None
                
                # Grupo
                group_id = group_map.get(provedor_id)
                
                # Parametros
                extra_params = params_by_device.get(legacy_id, {}).copy()
                password = extra_params.pop('password', 'SENHA_NAO_MIGRADA')
                
                device = Device(
                    tenant_id=tenant.id,
                    group_id=group_id,
                    device_type_id=device_type_id,
                    legacy_id=legacy_id,
                    name=nome.strip() if nome else f"Device_{legacy_id}",
                    ip_address=ip.strip() if ip else "0.0.0.0",
                    port=porta if porta else 22,
                    username=usuario if usuario else "admin",
                    password_encrypted=password,
                    use_telnet=use_telnet,
                    is_vpn_gateway=is_vpn_gateway,
                    backup_scheduled=backup_agendado,
                    extra_parameters=extra_params,
                    is_active=True
                )
                db.add(device)
                count += 1
                
                # Commit a cada 100 para evitar problemas de memoria
                if count % 100 == 0:
                    db.commit()
                    print(f"  {count} dispositivos inseridos...")
            
            except Exception as e:
                print(f"  ERRO no dispositivo {row[0]}: {e}")
                errors += 1
        
        db.commit()
        print(f"\n[OK] {count} dispositivos migrados com sucesso!")
        if errors:
            print(f"[WARN] {errors} erros")
        
    except Exception as e:
        print(f"ERRO: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()


if __name__ == '__main__':
    main()
