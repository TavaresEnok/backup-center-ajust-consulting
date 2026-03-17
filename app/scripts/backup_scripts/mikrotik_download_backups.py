# /srv/mikrotik_manager/backup_scripts/mikrotik_download_backups.py
# VERSÃO FINAL: Corrige o tratamento da saída de texto do comando 'file print' ao usar sshpass.

import os
import re
import subprocess
from typing import Tuple
from script_helpers import BackupLogger, sanitize_path_component

def run_command(command, logger, timeout=120):
    """Executa um comando no shell e loga o resultado."""
    try:
        # Esconde o comando real dos logs de processo para não expor a senha
        log_command = re.sub(r"sshpass -p '.*?'", "sshpass -p '********'", command)
        logger.emit(f"Executando comando nativo: {log_command}")
        
        proc = subprocess.run(command, shell=True, check=True, capture_output=True, text=True, timeout=timeout)
        return True, proc.stdout.strip()
    except subprocess.CalledProcessError as e:
        error_msg = f"Comando nativo falhou. Código: {e.returncode}\nStderr: {e.stderr.strip()}"
        logger.emit(error_msg, 'error')
        return False, error_msg
    except Exception as e:
        error_msg = f"Exceção inesperada ao executar comando nativo: {e}"
        logger.emit(error_msg, 'error')
        return False, error_msg

def realizar_backup(ip: str, usuario: str, porta: int, nome_provedor: str, nome_tipo_equip: str,
                      nome_dispositivo: str, parametros: dict = None, task_id: str = None, 
                      backup_base_path: str = None, **kwargs) -> Tuple:
    
    logger = BackupLogger(f"Downloader-{nome_dispositivo}", task_id)
    logger.emit("Iniciando tarefa de download via sshpass/scp...")

    password = (parametros or {}).get('password')
    delete_after_download = str((parametros or {}).get('delete_after_download', 'false')).lower() == 'true'

    if not password:
        msg = "Falha: 'password' é um parâmetro obrigatório."
        logger.emit(msg, 'error')
        return (False, msg, None, "CONFIGURACAO")

    dir_path = os.path.join(
        backup_base_path,
        sanitize_path_component(nome_provedor),
        sanitize_path_component(nome_tipo_equip),
        sanitize_path_component(nome_dispositivo)
    )
    os.makedirs(dir_path, exist_ok=True)

    ssh_opts = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
    sshpass_cmd_prefix = f"sshpass -p '{password}' ssh {ssh_opts} -p {porta} {usuario}@{ip}"

    try:
        logger.emit("Etapa 1/4: Listando ficheiros no dispositivo via SSH...")
        list_command = f"{sshpass_cmd_prefix} /file print"
        success, output = run_command(list_command, logger)
        if not success:
            raise Exception("Não foi possível conectar e listar os ficheiros no dispositivo.")

        logger.emit("Etapa 2/4: Processando a lista de ficheiros...")
        
        lines = output.splitlines()
        backup_files = []
        for line in lines:
            if line.strip().startswith('#') or line.strip().startswith('Flags:') or not line.strip():
                continue
            
            parts = line.strip().split()
            if len(parts) > 1:
                file_name = parts[1]
                if re.search(r'\.(backup|zip|rsc)$', file_name):
                    backup_files.append(file_name)

        if not backup_files:
            msg = "Nenhum ficheiro de backup (.backup, .zip, .rsc) encontrado no dispositivo."
            logger.emit(msg, "success")
            return (True, msg, dir_path)

        logger.emit(f"Encontrados {len(backup_files)} ficheiros de backup: {', '.join(backup_files)}", "success")

        logger.emit("Etapa 3/4: Iniciando download dos ficheiros via SCP...")
        downloaded_count = 0
        for file_name in backup_files:
            remote_path = f"{usuario}@{ip}:/{file_name}"
            local_path = os.path.join(dir_path, os.path.basename(file_name))
            scp_command = f"sshpass -p '{password}' scp {ssh_opts} -P {porta} \"{remote_path}\" \"{local_path}\""
            
            logger.emit(f"--> Baixando '{file_name}'...")
            success_scp, _ = run_command(scp_command, logger)
            if success_scp and os.path.exists(local_path):
                logger.emit(f"'{file_name}' baixado com sucesso.", "success")
                downloaded_count += 1
            else:
                logger.emit(f"Falha ao baixar '{file_name}'.", "error")

        if downloaded_count < len(backup_files):
            logger.emit(f"Aviso: {len(backup_files) - downloaded_count} ficheiro(s) não puderam ser baixados.", "warning")

        if downloaded_count == 0 and backup_files:
            raise Exception("Nenhum ficheiro pôde ser baixado com sucesso.")

        if delete_after_download:
            logger.emit("Etapa 4/4: Apagando ficheiros do dispositivo após o download...")
            for file_name in backup_files:
                local_path_check = os.path.join(dir_path, os.path.basename(file_name))
                if os.path.exists(local_path_check):
                    delete_command = f"{sshpass_cmd_prefix} /file remove [find name=\\\"{file_name}\\\"]"
                    logger.emit(f"--> Apagando '{file_name}'...")
                    run_command(delete_command, logger)
        else:
            logger.emit("Etapa 4/4: Ficheiros mantidos no dispositivo conforme configuração.", "info")

        msg = f"{downloaded_count} de {len(backup_files)} ficheiros de backup foram baixados com sucesso."
        logger.emit(msg, "success")
        return (True, msg, dir_path)

    except Exception as e:
        msg = f"Ocorreu um erro inesperado: {e}"
        logger.emit(msg, 'error')
        return (False, msg, None, "SCRIPT")
