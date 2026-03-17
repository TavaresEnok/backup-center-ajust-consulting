#!/usr/bin/env python3
"""
Migrate Legacy Backups Script - AJUST CONSULTING VERSION

Este script migra backups do sistema antigo para o novo Backup Center.
Todos os provedores são GRUPOS do tenant ajust-consulting.

Estrutura antiga: {ProviderName}/{DeviceType}/{DeviceName}/backup.rsc
Estrutura nova: ajust-consulting/{GroupName}/{DeviceName}/backup.rsc
"""

import os
import sys
import re
import shutil
import hashlib
import argparse
import uuid
from datetime import datetime
from pathlib import Path

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERRO: psycopg2 não instalado. Execute: pip3 install psycopg2-binary")
    sys.exit(1)

# Paths
SCRIPT_DIR = Path(__file__).parent
LEGACY_BACKUP_DIR = SCRIPT_DIR / "backups_sistema_antigo"
NEW_BACKUP_DIR = SCRIPT_DIR / "storage" / "backups"

# Database config
DB_CONFIG = {
    'host': os.getenv('DB_HOST', '127.0.0.1'),
    'port': int(os.getenv('DB_PORT', 5433)),
    'database': os.getenv('DB_NAME', 'backup_center'),
    'user': os.getenv('DB_USER', 'backup_user'),
    'password': os.getenv('DB_PASSWORD', 'BackupSecure2024!')
}

# Ajust Consulting Tenant slug
AJUST_TENANT_SLUG = 'ajust-consulting'

# Estatísticas
stats = {
    'files_found': 0,
    'files_migrated': 0,
    'files_skipped_exists': 0,
    'files_no_group': 0,
    'files_no_device': 0,
    'files_error': 0,
    'groups_created': 0,
    'devices_created': 0,
}

# Cache
group_cache = {}
device_cache = {}
device_type_cache = {}


def sanitize_name(name: str) -> str:
    if not name:
        return ""
    return "".join(c for c in name if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')


def normalize_for_match(name: str) -> str:
    if not name:
        return ""
    return re.sub(r'[\s_-]+', '', name.lower())


def parse_date_from_filename(filename: str) -> datetime:
    match = re.search(r'(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})', filename)
    if match:
        try:
            return datetime.strptime(match.group(1), '%Y-%m-%d_%H-%M-%S')
        except ValueError:
            pass
    match = re.search(r'(\d{4}-\d{2}-\d{2})', filename)
    if match:
        try:
            return datetime.strptime(match.group(1), '%Y-%m-%d')
        except ValueError:
            pass
    return None


def get_db_connection():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = False
        return conn
    except Exception as e:
        print(f"ERRO ao conectar ao banco: {e}")
        sys.exit(1)


def get_ajust_tenant(cursor):
    """Retorna o tenant ajust-consulting."""
    cursor.execute("SELECT id, name, slug FROM tenants WHERE slug = %s", (AJUST_TENANT_SLUG,))
    return cursor.fetchone()


def load_groups(cursor, tenant_id):
    """Carrega todos os grupos do tenant no cache."""
    cursor.execute("SELECT id, name, slug FROM device_groups WHERE tenant_id = %s", (tenant_id,))
    for g in cursor.fetchall():
        key = normalize_for_match(g['name'])
        group_cache[key] = g
        # Também adiciona pelo slug
        key_slug = normalize_for_match(g['slug'])
        group_cache[key_slug] = g


def find_group(provider_folder: str):
    """Encontra grupo pelo nome do provedor."""
    normalized = normalize_for_match(provider_folder)
    
    if normalized in group_cache:
        return group_cache[normalized]
    
    # Tenta match parcial
    for key, group in group_cache.items():
        if normalized in key or key in normalized:
            return group
    
    return None


def create_group(cursor, tenant_id, group_name: str):
    """Cria novo grupo."""
    sanitized = sanitize_name(group_name)
    slug = sanitized.lower().replace(' ', '-').replace('_', '-')[:150]
    new_id = str(uuid.uuid4())
    
    cursor.execute("""
        INSERT INTO device_groups (id, tenant_id, name, slug, description, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, true, NOW(), NOW())
        RETURNING id, name, slug
    """, (new_id, tenant_id, sanitized, slug, f'Grupo migrado: {group_name}'))
    
    new_group = cursor.fetchone()
    stats['groups_created'] += 1
    
    key = normalize_for_match(new_group['name'])
    group_cache[key] = new_group
    
    return new_group


def load_devices(cursor, tenant_id):
    """Carrega todos os devices do tenant no cache."""
    cursor.execute("""
        SELECT d.id, d.name, d.group_id, d.last_backup_at, dg.name as group_name
        FROM devices d
        LEFT JOIN device_groups dg ON d.group_id = dg.id
        WHERE d.tenant_id = %s
    """, (tenant_id,))
    
    for d in cursor.fetchall():
        # Cache por group + device name
        if d['group_name']:
            key = f"{normalize_for_match(d['group_name'])}_{normalize_for_match(d['name'])}"
            device_cache[key] = d
        # Também cache só pelo device name (para matching flexível)
        key_name = normalize_for_match(d['name'])
        if key_name not in device_cache:
            device_cache[key_name] = d


def find_device(group_name: str, device_folder: str):
    """Encontra device pelo grupo e nome."""
    group_norm = normalize_for_match(group_name)
    device_norm = normalize_for_match(device_folder)
    
    # Tenta match exato por group + device
    key = f"{group_norm}_{device_norm}"
    if key in device_cache:
        return device_cache[key]
    
    # Tenta match só pelo device name
    if device_norm in device_cache:
        return device_cache[device_norm]
    
    # Tenta match parcial
    for cache_key, device in device_cache.items():
        if device_norm in cache_key or cache_key.endswith(f"_{device_norm}"):
            return device
    
    return None


def load_device_types(cursor):
    """Carrega device types no cache."""
    cursor.execute("SELECT id, name, slug FROM device_types")
    for dt in cursor.fetchall():
        device_type_cache[normalize_for_match(dt['name'])] = dt
        device_type_cache[normalize_for_match(dt['slug'])] = dt


def find_device_type(device_type_folder: str):
    """Encontra device type pela pasta."""
    normalized = normalize_for_match(device_type_folder)
    
    if normalized in device_type_cache:
        return device_type_cache[normalized]
    
    folder_lower = device_type_folder.lower()
    
    # Match por keywords
    for key, dt in device_type_cache.items():
        if 'mikrotik' in folder_lower and 'mikrotik' in key:
            return dt
        if 'huawei' in folder_lower and 'olt' in folder_lower and 'huawei' in key and 'olt' in key:
            return dt
        if 'huawei' in folder_lower and 'switch' in folder_lower and 'switch' in key and 'huawei' in key:
            return dt
        if 'huawei' in folder_lower and ('router' in folder_lower or 'ne8000' in folder_lower or 'ne40' in folder_lower):
            if 'router' in key or 'ne' in key:
                return dt
        if 'datacom' in folder_lower and 'datacom' in key:
            return dt
        if 'zte' in folder_lower and 'zte' in key:
            return dt
        if 'vsol' in folder_lower and 'vsol' in key:
            return dt
        if 'fiberhome' in folder_lower and 'fiberhome' in key:
            return dt
        if 'zabbix' in folder_lower and 'zabbix' in key:
            return dt
        if 'grafana' in folder_lower and 'grafana' in key:
            return dt
    
    return None


def create_device(cursor, tenant_id, group_id, device_type_id, device_name: str):
    """Cria novo device."""
    sanitized = sanitize_name(device_name)
    new_id = str(uuid.uuid4())
    
    # Senha placeholder criptografada
    dummy_password = "gAAAAABnhIWK8e7BI-P4H0kBTe9LwxhVQ8K9_C3Yp8yJK5_9-mZHF9ZL3c4Pq2OQ0x1Yd-EuHvVn85K-eA2mR_tY3Oc1R-9Xyw=="
    
    cursor.execute("""
        INSERT INTO devices (id, tenant_id, group_id, device_type_id, name, ip_address, port, 
                            username, password_encrypted, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true, NOW(), NOW())
        RETURNING id, name, group_id, last_backup_at
    """, (new_id, tenant_id, group_id, device_type_id, sanitized, '0.0.0.0', 22, 'admin', dummy_password))
    
    new_device = cursor.fetchone()
    stats['devices_created'] += 1
    
    return new_device


def get_file_hash(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def backup_exists_in_db(cursor, file_path: str) -> bool:
    cursor.execute("SELECT 1 FROM backups WHERE file_path = %s", (file_path,))
    return cursor.fetchone() is not None


def migrate_backup_file(cursor, file_path: Path, device, group_name: str, dry_run: bool = False) -> bool:
    """Migra um arquivo de backup."""
    
    new_dir = NEW_BACKUP_DIR / AJUST_TENANT_SLUG / sanitize_name(group_name) / sanitize_name(device['name'])
    new_file_path = new_dir / file_path.name
    
    rel_path = f"{AJUST_TENANT_SLUG}/{sanitize_name(group_name)}/{sanitize_name(device['name'])}/{file_path.name}"
    
    if backup_exists_in_db(cursor, rel_path):
        stats['files_skipped_exists'] += 1
        return False
    
    if dry_run:
        stats['files_migrated'] += 1
        return True
    
    new_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(file_path, new_file_path)
    
    backup_date = parse_date_from_filename(file_path.name)
    if not backup_date:
        backup_date = datetime.fromtimestamp(file_path.stat().st_mtime)
    
    file_size = new_file_path.stat().st_size
    file_hash = get_file_hash(new_file_path)
    
    backup_id = str(uuid.uuid4())
    cursor.execute("""
        INSERT INTO backups (id, device_id, file_path, file_size_bytes, hash_sha256, status, 
                            created_at, completed_at, started_at, is_manual)
        VALUES (%s, %s, %s, %s, %s, 'success', %s, %s, %s, false)
    """, (backup_id, device['id'], rel_path, file_size, file_hash, backup_date, backup_date, backup_date))
    
    if not device['last_backup_at'] or backup_date > device['last_backup_at']:
        cursor.execute("UPDATE devices SET last_backup_at = %s, last_backup_status = 'success' WHERE id = %s",
                      (backup_date, device['id']))
    
    stats['files_migrated'] += 1
    return True


def scan_legacy_backups(dry_run: bool = False, limit: int = None, verbose: bool = False, create_missing: bool = True):
    """Escaneia e migra todos os backups legados."""
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    processed = 0
    
    try:
        print(f"\n{'='*60}")
        print(f"MIGRAÇÃO DE BACKUPS LEGADOS - AJUST CONSULTING")
        print(f"{'='*60}")
        print(f"Origem: {LEGACY_BACKUP_DIR}")
        print(f"Destino: {NEW_BACKUP_DIR}/{AJUST_TENANT_SLUG}/")
        print(f"Modo: {'DRY-RUN (simulação)' if dry_run else 'PRODUÇÃO'}")
        print(f"Criar grupos/devices: {'SIM' if create_missing else 'NÃO'}")
        if limit:
            print(f"Limite: {limit} arquivos")
        print(f"{'='*60}\n")
        
        if not LEGACY_BACKUP_DIR.exists():
            print(f"ERRO: Diretório de origem não encontrado: {LEGACY_BACKUP_DIR}")
            return
        
        # Carrega tenant ajust-consulting
        ajust_tenant = get_ajust_tenant(cursor)
        if not ajust_tenant:
            print(f"ERRO: Tenant '{AJUST_TENANT_SLUG}' não encontrado!")
            return
        
        print(f"[INFO] Tenant: {ajust_tenant['name']} (ID: {ajust_tenant['id'][:8]}...)")
        
        # Carrega caches
        load_groups(cursor, ajust_tenant['id'])
        load_devices(cursor, ajust_tenant['id'])
        load_device_types(cursor)
        
        print(f"[INFO] Grupos carregados: {len(group_cache)}")
        print(f"[INFO] Devices carregados: {len(device_cache)}")
        print(f"[INFO] Tipos de device: {len(device_type_cache)}\n")
        
        unmatched_groups = set()
        unmatched_devices = []
        
        # Itera: ProviderFolder/DeviceTypeFolder/DeviceFolder/files
        for provider_folder in sorted(LEGACY_BACKUP_DIR.iterdir()):
            if not provider_folder.is_dir():
                continue
            if limit and processed >= limit:
                break
            
            provider_name = provider_folder.name
            group = find_group(provider_name)
            
            if not group and create_missing and not dry_run:
                group = create_group(cursor, ajust_tenant['id'], provider_name)
                if verbose:
                    print(f"[NEW GROUP] {provider_name}")
            
            if not group:
                unmatched_groups.add(provider_name)
                # Conta arquivos não migrados
                for dt_folder in provider_folder.iterdir():
                    if dt_folder.is_dir():
                        for dev_folder in dt_folder.iterdir():
                            if dev_folder.is_dir():
                                stats['files_no_group'] += len(list(dev_folder.glob('*.*')))
                continue
            
            if verbose:
                print(f"\n[GRUPO] {provider_name} -> {group['name']}")
            
            for device_type_folder in sorted(provider_folder.iterdir()):
                if not device_type_folder.is_dir():
                    continue
                if limit and processed >= limit:
                    break
                
                device_type_name = device_type_folder.name
                device_type = find_device_type(device_type_name)
                device_type_id = device_type['id'] if device_type else None
                
                if verbose:
                    type_info = f"-> {device_type['name']}" if device_type else "(sem tipo)"
                    print(f"  [TIPO] {device_type_name} {type_info}")
                
                for device_folder in sorted(device_type_folder.iterdir()):
                    if not device_folder.is_dir():
                        continue
                    if limit and processed >= limit:
                        break
                    
                    device_name = device_folder.name
                    device = find_device(group['name'], device_name)
                    
                    if not device and create_missing and not dry_run:
                        device = create_device(cursor, ajust_tenant['id'], group['id'], device_type_id, device_name)
                        if verbose:
                            print(f"    [NEW DEVICE] {device_name}")
                    
                    if not device:
                        backup_files = list(device_folder.glob('*.rsc')) + list(device_folder.glob('*.backup')) + list(device_folder.glob('*.cfg')) + list(device_folder.glob('*.zip'))
                        file_count = len(backup_files)
                        stats['files_no_device'] += file_count
                        if file_count > 0:
                            unmatched_devices.append(f"{provider_name}/{device_type_name}/{device_name} ({file_count})")
                        continue
                    
                    backup_files = list(device_folder.glob('*.rsc')) + list(device_folder.glob('*.backup')) + list(device_folder.glob('*.cfg')) + list(device_folder.glob('*.zip'))
                    
                    for backup_file in sorted(backup_files):
                        stats['files_found'] += 1
                        if limit and processed >= limit:
                            break
                        
                        try:
                            if migrate_backup_file(cursor, backup_file, device, group['name'], dry_run):
                                if verbose:
                                    print(f"      [OK] {backup_file.name}")
                            processed += 1
                        except Exception as e:
                            stats['files_error'] += 1
                            print(f"      [ERROR] {backup_file.name}: {e}")
        
        if not dry_run:
            conn.commit()
            print("\n[OK] Alterações salvas no banco de dados.")
        else:
            conn.rollback()
        
        # Relatório
        print(f"\n{'='*60}")
        print("RELATÓRIO FINAL")
        print(f"{'='*60}")
        print(f"Arquivos encontrados:        {stats['files_found']}")
        print(f"Arquivos migrados:           {stats['files_migrated']}")
        print(f"Arquivos já existentes:      {stats['files_skipped_exists']}")
        print(f"Sem grupo correspondente:    {stats['files_no_group']}")
        print(f"Sem device correspondente:   {stats['files_no_device']}")
        print(f"Erros:                       {stats['files_error']}")
        print(f"Grupos criados:              {stats['groups_created']}")
        print(f"Devices criados:             {stats['devices_created']}")
        print(f"{'='*60}")
        
        if unmatched_groups:
            print(f"\n[INFO] Grupos não mapeados ({len(unmatched_groups)}):")
            for g in sorted(unmatched_groups)[:15]:
                print(f"  - {g}")
        
        if unmatched_devices and verbose:
            log_file = SCRIPT_DIR / "migration_unmatched_devices.log"
            with open(log_file, 'w') as f:
                for d in sorted(unmatched_devices):
                    f.write(f"{d}\n")
            print(f"\n[INFO] Devices não mapeados salvos em: {log_file}")
        
    except Exception as e:
        conn.rollback()
        print(f"\n[FATAL ERROR] {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        cursor.close()
        conn.close()


def main():
    parser = argparse.ArgumentParser(description='Migra backups legados para o Backup Center (Ajust Consulting)')
    parser.add_argument('--dry-run', action='store_true', help='Simula sem fazer alterações')
    parser.add_argument('--limit', type=int, help='Limite de arquivos')
    parser.add_argument('--verbose', '-v', action='store_true', help='Modo verboso')
    parser.add_argument('--no-create', action='store_true', help='Não criar grupos/devices automaticamente')
    parser.add_argument('--db-host', default='127.0.0.1')
    parser.add_argument('--db-port', type=int, default=5433)
    
    args = parser.parse_args()
    
    DB_CONFIG['host'] = args.db_host
    DB_CONFIG['port'] = args.db_port
    
    scan_legacy_backups(
        dry_run=args.dry_run,
        limit=args.limit,
        verbose=args.verbose,
        create_missing=not args.no_create
    )


if __name__ == "__main__":
    main()
