# /srv/mikrotik_manager/backup_scripts/huawei_ne_router_netmiko.py
# VERSÃO FINAL COM TESTE DE CONEXÃO

import os
import time
import re
import zipfile
from typing import Tuple
from netmiko import ConnectHandler
from script_helpers import BackupLogger, sanitize_path_component

def realizar_backup(ip: str, usuario: str, porta: int, nome_provedor: str, nome_tipo_equip: str,
                      nome_dispositivo: str, parametros: dict = None, task_id: str = None, 
                      backup_base_path: str = None, **kwargs) -> Tuple[bool, str, str]:

    logger = BackupLogger(nome_dispositivo, task_id)
    logger.emit("Iniciando backup para Huawei NE...")
    
    password = (parametros or {}).get('password')
    if not password:
        msg = "Falha: 'password' é um parâmetro obrigatório."
        logger.emit(msg, 'error')
        return (False, msg, None, "CONFIGURACAO")

    device_config = {
        'device_type': 'huawei',
        'host': ip,
        'port': int(porta),
        'username': usuario,
        'password': password,
        'conn_timeout': 60,
        'banner_timeout': 60,
        'auth_timeout': 60,
        'fast_cli': False,
    }
    
    # --- PASSO 1: TESTE DE CONEXÃO ---
    logger.emit("Etapa 1/4: Testando conexão inicial...")
    try:
        with ConnectHandler(**device_config) as test_connect:
            logger.emit("Teste de conexão bem-sucedido.", 'success')
    except Exception as exc:
        msg = f"A conexão foi fechada, recusada ou as credenciais estão incorretas. Detalhe: {type(exc).__name__}: {exc}"
        logger.emit(msg, 'error')
        return (False, msg, None, "AUTENTICACAO")

    # --- PASSO 2: BACKUP DO ADMIN-VS E DESCOBERTA DE OUTROS VSs ---
    caminho_final_backup = os.path.join(
        backup_base_path,
        sanitize_path_component(nome_provedor),
        sanitize_path_component(nome_tipo_equip),
        sanitize_path_component(nome_dispositivo)
    )
    os.makedirs(caminho_final_backup, exist_ok=True)
    
    timestamp = time.strftime('%Y-%m-%d_%H-%M-%S')
    arquivos_temporarios_cfg, vs_ignoradas, virtual_systems_encontrados = [], [], []

    try:
        logger.emit("Etapa 2/4: Conectando para backup do Admin-VS...")
        with ConnectHandler(**device_config) as net_connect:
            net_connect.send_command("screen-length 0 temporary", expect_string=r'>')
            
            logger.emit("Fazendo backup do Admin-VS...")
            config_admin_vs = net_connect.send_command('display current-configuration', read_timeout=300)
            caminho_admin = os.path.join(caminho_final_backup, f"backup_{timestamp}_Admin-VS.cfg")
            with open(caminho_admin, 'w', encoding='utf-8') as f:
                f.write(config_admin_vs)
            arquivos_temporarios_cfg.append(caminho_admin)
            
            logger.emit("Descobrindo Virtual-Systems...")
            output = net_connect.send_command('switch virtual-system ?', read_timeout=90)
            virtual_systems_encontrados = re.findall(r'^\s+([A-Za-z0-9_-]+)\s+Name of virtual system', output, re.MULTILINE)
            if virtual_systems_encontrados:
                logger.emit(f"VSs descobertos: {', '.join(virtual_systems_encontrados)}", 'success')
    except Exception as e:
        msg = f"Falha na Etapa 2 (Admin-VS): {type(e).__name__}: {e}"
        logger.emit(msg, 'error')
        return (False, msg, None, "SCRIPT")

    # --- PASSO 3: BACKUP INDIVIDUAL DE CADA VS ---
    logger.emit("Etapa 3/4: Iniciando backup individual de cada VS...")
    for vs_name in virtual_systems_encontrados:
        try:
            with ConnectHandler(**device_config) as net_connect_vs:
                net_connect_vs.send_command("screen-length 0 temporary", expect_string=r'>')
                
                logger.emit(f"Entrando na VS '{vs_name}'...")
                net_connect_vs.send_command_timing(f"switch virtual-system {vs_name}")
                time.sleep(2)
                
                config_vs = net_connect_vs.send_command('display current-configuration', read_timeout=300)
                
                caminho_vs = os.path.join(caminho_final_backup, f"backup_{timestamp}_{sanitize_path_component(vs_name)}.cfg")
                with open(caminho_vs, 'w', encoding='utf-8') as f:
                    f.write(config_vs)
                arquivos_temporarios_cfg.append(caminho_vs)
                logger.emit(f"Backup da VS '{vs_name}' concluído.", 'success')
        except Exception as vs_e:
            logger.emit(f"Erro ao processar a VS '{vs_name}': {type(vs_e).__name__}: {vs_e}. Pulando.", 'error')
            vs_ignoradas.append(vs_name)
            continue
    
    # --- PASSO 4: FINALIZAÇÃO (COMPACTAÇÃO) ---
    logger.emit("Etapa 4/4: Finalizando e compactando ficheiros...")
    try:
        if not arquivos_temporarios_cfg:
            raise Exception("Nenhum arquivo de backup pôde ser gerado.")
            
        caminho_zip = os.path.join(caminho_final_backup, f"backup_{timestamp}_consolidado.zip")
        with zipfile.ZipFile(caminho_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file_path in arquivos_temporarios_cfg:
                zf.write(file_path, os.path.basename(file_path))
        
        for file_path in arquivos_temporarios_cfg:
            os.remove(file_path)
            
        msg = f"Backup de {len(arquivos_temporarios_cfg)} configurações criado com sucesso."
        if vs_ignoradas:
            msg += f" | VS ignoradas: {', '.join(vs_ignoradas)}."
            
        logger.emit(msg, 'success')
        return (True, msg, caminho_zip)
        
    except Exception as e:
        error_msg = f"Falha crítica na finalização: {type(e).__name__}: {e}"
        logger.emit(error_msg, 'error')
        return (False, error_msg, None, "SCRIPT")
