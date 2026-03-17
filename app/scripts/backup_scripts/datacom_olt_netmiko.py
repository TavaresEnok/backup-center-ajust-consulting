import re
from typing import Tuple

from netmiko import ConnectHandler

from script_helpers import BackupLogger, prepare_backup_path

def realizar_backup(ip: str, usuario: str, porta: int, nome_provedor: str, nome_tipo_equip: str,
                    nome_dispositivo: str, parametros: dict = None, task_id: str = None, 
                    backup_base_path: str = None, **kwargs) -> Tuple:

    logger = BackupLogger(nome_dispositivo, task_id)
    
    # Compatibilidade com o projeto novo: timeout vem de parametros, sem depender de app.utils.get_setting
    try:
        timeout_value = int((parametros or {}).get("backup_connection_timeout", 60))
    except (ValueError, TypeError):
        timeout_value = 60
    
    logger.emit(f"Iniciando backup para OLT Datacom (timeout: {timeout_value}s)...")
    
    password = (parametros or {}).get('password')
    if not password:
        msg = "Falha: 'password' é um parâmetro obrigatório."
        logger.emit(msg, 'error')
        return (False, msg, None, "CONFIGURACAO")

    device_config = {
        'device_type': 'cisco_ios_telnet', 
        'host': ip, 'port': int(porta), 
        'username': usuario, 
        'password': password, 
        'fast_cli': False, 
        'conn_timeout': timeout_value
    }
    
    logger.emit("Etapa 1/4: Testando conexão...")
    try:
        with ConnectHandler(**device_config) as test_connect:
            logger.emit("Teste de conexão bem-sucedido.", 'success')
    except Exception as exc:
        msg = f"A conexão foi fechada, recusada ou as credenciais estão incorretas. Detalhe: {type(exc).__name__}: {exc}"
        logger.emit(msg, 'error')
        return (False, msg, None, "AUTENTICACAO")

    caminho_local_completo = prepare_backup_path(
        backup_base_path, nome_provedor, nome_tipo_equip, nome_dispositivo, 'cfg'
    )

    try:
        logger.emit("Etapa 2/4: Reconectando para realizar o backup...")
        with ConnectHandler(**device_config) as net_connect:
            prompt = net_connect.find_prompt()
            logger.emit("Etapa 3/4: Desativando paginação...")
            net_connect.send_command("paginate false", expect_string=re.escape(prompt.strip()), read_timeout=20)
            logger.emit("Etapa 4/4: Executando 'show running-config'...")
            output = net_connect.send_command('show running-config', read_timeout=300)
            logger.emit("Coleta da configuração concluída.")

            if not output or 'invalid input detected' in output.lower():
                raise ValueError("O dispositivo não retornou uma configuração válida.")

        with open(caminho_local_completo, 'w', encoding='utf-8') as f:
            f.write(output)
            
        msg = f"Backup da OLT Datacom '{nome_dispositivo}' concluído!"
        logger.emit(msg, 'success')
        return (True, msg, caminho_local_completo)
        
    except Exception as e:
        error_msg = f"Falha inesperada durante o backup: {e}"
        logger.emit(error_msg, 'error')
        return (False, error_msg, None, "SCRIPT")
