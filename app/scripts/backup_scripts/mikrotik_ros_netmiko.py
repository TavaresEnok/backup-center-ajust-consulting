from typing import Tuple

from netmiko import ConnectHandler

from script_helpers import BackupLogger, prepare_backup_path


ERROR_MARKERS = ("bad command", "syntax error", "expected end of command")


def _invalid(output: str) -> bool:
    text = (output or "").lower()
    return any(marker in text for marker in ERROR_MARKERS)


def realizar_backup(
    ip: str,
    usuario: str,
    porta: int,
    nome_provedor: str,
    nome_tipo_equip: str,
    nome_dispositivo: str,
    parametros: dict = None,
    task_id: str = None,
    backup_base_path: str = None,
    **kwargs,
) -> Tuple:
    logger = BackupLogger(nome_dispositivo, task_id)
    logger.emit(f"Iniciando backup para MikroTik: {nome_dispositivo}")

    password = (parametros or {}).get("password")
    if not password:
        msg = "Falha: 'password' e um parametro obrigatorio."
        logger.emit(msg, "error")
        return (False, msg, None, "CONFIGURACAO")

    device_config = {
        "device_type": "mikrotik_routeros",
        "host": ip,
        "port": int(porta),
        "username": usuario,
        "password": password,
        "conn_timeout": 25,
        "banner_timeout": 25,
        "auth_timeout": 25,
        "fast_cli": False,
        "global_delay_factor": 2,
    }

    logger.emit("Etapa 1/3: Testando conexao...")
    try:
        with ConnectHandler(**device_config):
            logger.emit("Teste de conexao bem-sucedido.", "success")
    except Exception as exc:
        msg = f"A conexao foi fechada, recusada ou as credenciais estao incorretas. Detalhe: {type(exc).__name__}: {exc}"
        logger.emit(msg, "error")
        return (False, msg, None, "AUTENTICACAO")

    caminho_local = prepare_backup_path(backup_base_path, nome_provedor, nome_tipo_equip, nome_dispositivo, "rsc")

    try:
        logger.emit("Etapa 2/3: Reconectando para realizar o backup...")
        with ConnectHandler(**device_config) as net_connect:
            commands = ["/export terse", "/export", "export terse", "export"]
            output = ""
            used_cmd = None

            for cmd in commands:
                try:
                    logger.emit(f"Executando '{cmd}'...")
                    out = net_connect.send_command_timing(
                        command_string=cmd,
                        read_timeout=600,
                        strip_command=False,
                        strip_prompt=False,
                    )
                    if out and not _invalid(out) and len(out.strip()) > len(output.strip()):
                        output = out
                        used_cmd = cmd
                    if out and not _invalid(out) and len(out.strip()) > 80:
                        break
                except Exception:
                    continue

            if not output or _invalid(output) or len(output.strip()) < 80:
                raise RuntimeError("O dispositivo nao retornou configuracao valida.")

        logger.emit("Etapa 3/3: Salvando arquivo de backup...")
        with open(caminho_local, "w", encoding="utf-8") as fp:
            fp.write(output)

        msg = f"Backup do MikroTik '{nome_dispositivo}' concluido!"
        if used_cmd:
            msg = f"{msg} ({used_cmd})"
        logger.emit(msg, "success")
        return (True, msg, caminho_local)
    except Exception as exc:
        msg = f"Erro inesperado durante o backup: {exc}"
        logger.emit(msg, "error")
        return (False, msg, None, "SCRIPT")
