import os
from typing import Tuple
from netmiko import ConnectHandler
from script_helpers import BackupLogger, prepare_backup_path

def realizar_backup(ip: str, usuario: str, porta: int, nome_provedor: str, nome_tipo_equip: str,
                      nome_dispositivo: str, parametros: dict = None, task_id: str = None, 
                      backup_base_path: str = None, **kwargs) -> Tuple:
    
    logger = BackupLogger(nome_dispositivo, task_id)
    logger.emit(f"Iniciando backup para {nome_dispositivo} ({nome_tipo_equip})...")
    
    password = (parametros or {}).get('password')
    if not password:
        msg = "Falha: 'password' é um parâmetro obrigatório."
        logger.emit(msg, 'error')
        return (False, msg, None, "CONFIGURACAO")

    device_config = {
        'device_type': 'linux',
        'host': ip,
        'port': int(porta),
        'username': usuario,
        'password': password,
        'global_delay_factor': 2,
    }
    
    logger.emit("Etapa 1/3: Testando conexão e autenticando...")
    try:
        with ConnectHandler(**device_config) as net_connect:
            logger.emit("Teste de conexão bem-sucedido.", 'success')
            logger.emit("Etapa 2/3: Executando comando de coleta 'uname -a && uptime && ip route'...")
            
            output = net_connect.send_command(
                command_string='uname -a && uptime && ip route',
                read_timeout=120
            )
            logger.emit("Coleta da configuração concluída.")
            
            if not output or len(output.strip()) < 10:
                 raise ValueError("O dispositivo não retornou uma configuração válida.")

            logger.emit("Etapa 3/3: Salvando arquivo de backup...")
            ext = 'txt'
            if 'linux' == 'pfsense_os': ext = 'xml'
            caminho_local = prepare_backup_path(backup_base_path, nome_provedor, nome_tipo_equip, nome_dispositivo, ext)
            
            with open(caminho_local, 'w', encoding='utf-8') as f:
                f.write(output)
            
            msg = f"Backup concluído com sucesso!"
            logger.emit(msg, 'success')
            return (True, msg, caminho_local)
            
    except Exception as e:
        msg = f"Erro iteragindo com o equipamento: {e}"
        logger.emit(msg, 'error')
        return (False, msg, None, "AUTENTICACAO" if "auth" in str(e).lower() else "SCRIPT")
