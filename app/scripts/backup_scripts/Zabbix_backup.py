import os
import paramiko
from typing import Tuple
from urllib.parse import quote_plus

from netmiko import ConnectHandler

from script_helpers import BackupLogger, prepare_backup_path


def _is_privileged_prompt(output: str) -> bool:
    text = (output or "").strip()
    return text.endswith("#")


def _become_root(net_connect, secrets, logger) -> bool:
    """Tenta elevar privilegio de forma tolerante para ambientes legados."""
    logger.emit("A obter acesso root...", "info")

    probe = net_connect.send_command_timing(
        "whoami",
        read_timeout=15,
        strip_command=False,
        strip_prompt=False,
    )
    if "root" in (probe or "").lower():
        logger.emit("Sessao ja esta com privilegios de root.", "success")
        return True

    candidates = []
    for item in secrets:
        value = (item or "").strip()
        if value and value not in candidates:
            candidates.append(value)

    for cmd in ("su -", "sudo -s", "sudo su -"):
        try:
            output = net_connect.send_command_timing(
                cmd,
                read_timeout=20,
                strip_command=False,
                strip_prompt=False,
            )

            # Alguns hosts voltam direto para prompt privilegiado.
            if _is_privileged_prompt(output):
                logger.emit("Privilegios de root obtidos com sucesso.", "success")
                return True

            lower = (output or "").lower()
            if "password" in lower or "senha" in lower:
                for secret in candidates:
                    ans = net_connect.send_command_timing(
                        secret,
                        read_timeout=20,
                        strip_command=False,
                        strip_prompt=False,
                    )
                    if _is_privileged_prompt(ans):
                        logger.emit("Privilegios de root obtidos com sucesso.", "success")
                        return True
                    if "sorry" in (ans or "").lower() or "incorrect" in (ans or "").lower():
                        continue
        except Exception:
            continue

    logger.emit("Nao foi possivel obter root; tentando continuar sem elevacao.", "warning")
    return False


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
    logger.emit("Iniciando backup do banco de dados Zabbix...")

    parametros = parametros or {}
    req_params = ["password", "db_name", "db_user", "db_password", "db_type"]
    if not all(k in parametros and parametros[k] for k in req_params):
        msg = f"Falha: Parametros obrigatorios ausentes. Necessario: {', '.join(req_params)}."
        logger.emit(msg, "error")
        return (False, msg, None, "CONFIGURACAO")

    login_password = parametros["password"]
    root_password = parametros.get("root_password")
    enable_password = parametros.get("enable_password")

    device_config = {
        "device_type": "linux",
        "host": ip,
        "port": int(porta),
        "username": usuario,
        "password": login_password,
        "conn_timeout": 35,
        "banner_timeout": 35,
        "auth_timeout": 35,
        "fast_cli": False,
    }

    logger.emit("Etapa 1/4: Testando conexao SSH inicial...")
    try:
        with ConnectHandler(**device_config) as test_connect:
            if not test_connect.is_alive():
                raise RuntimeError("A conexao SSH inicial falhou.")
        logger.emit("Teste de conexao SSH bem-sucedido.", "success")
    except Exception as e:
        msg = f"A conexao SSH foi fechada ou a senha do usuario '{usuario}' esta incorreta. Erro: {e}"
        logger.emit(msg, "error")
        return (False, msg, None, "AUTENTICACAO")

    db_type = parametros.get("db_type", "").lower()
    db_name = parametros["db_name"]
    db_user = parametros["db_user"]
    db_password = parametros["db_password"]
    exclude_tables = [t.strip() for t in parametros.get("exclude_tables", "").split(",") if t.strip()]

    if db_type == "postgres":
        exclude_flags = " ".join([f"--exclude-table={t}" for t in exclude_tables])
        conn_str = f"postgresql://{quote_plus(db_user)}:{quote_plus(db_password)}@localhost/{db_name}"
        dump_base = f"pg_dump --dbname='{conn_str}' {exclude_flags}"
    else:
        exclude_flags = " ".join([f"--ignore-table={db_name}.{t}" for t in exclude_tables])
        dump_base = f"mysqldump --single-transaction -h localhost -u {db_user} -p'{db_password}' {db_name} {exclude_flags}"

    remote_filepath = f"/tmp/backup_{os.urandom(8).hex()}.sql.gz"
    dump_command = f"{dump_base} | gzip > {remote_filepath}"

    secrets = [root_password, enable_password, login_password, usuario]

    try:
        logger.emit("Etapa 2/4: Executando dump remoto...")
        with ConnectHandler(**device_config) as net_connect:
            _become_root(net_connect, secrets, logger)
            output = net_connect.send_command_timing(
                dump_command,
                read_timeout=3600,
                strip_command=False,
                strip_prompt=False,
            )
            upper = (output or "").upper()
            if output and any(err in upper for err in ["ERROR", "FAILED", "NOT FOUND", "PERMISSION DENIED"]):
                raise RuntimeError(f"Comando de dump retornou um erro: {output}")

        logger.emit("Dump remoto concluido com sucesso.", "success")
    except Exception as e:
        msg = f"Falha na Etapa 2 (Criacao do Dump): {e}"
        logger.emit(msg, "error")
        return (False, msg, None, "SCRIPT")

    local_filepath = prepare_backup_path(
        backup_base_path,
        nome_provedor,
        nome_tipo_equip,
        nome_dispositivo,
        "sql.gz",
    )

    try:
        logger.emit("Etapa 3/4: Iniciando transferencia SFTP...")
        with paramiko.SSHClient() as ssh_client:
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh_client.connect(
                hostname=ip,
                port=int(porta),
                username=usuario,
                password=login_password,
                timeout=35,
                banner_timeout=35,
                auth_timeout=35,
                look_for_keys=False,
                allow_agent=False,
            )
            with ssh_client.open_sftp() as sftp_client:
                sftp_client.get(remote_filepath, local_filepath)

        if not os.path.exists(local_filepath) or os.path.getsize(local_filepath) == 0:
            raise RuntimeError("Arquivo nao foi baixado ou esta vazio.")
        logger.emit("Transferencia concluida.", "success")
    except Exception as e:
        msg = f"Falha na Etapa 3 (Transferencia SFTP): {e}"
        logger.emit(msg, "error")
        return (False, msg, None, "SCRIPT")

    try:
        logger.emit("Etapa 4/4: Limpando arquivo temporario remoto...")
        with ConnectHandler(**device_config) as net_connect:
            _become_root(net_connect, secrets, logger)
            net_connect.send_command_timing(
                f"rm -f {remote_filepath}",
                read_timeout=60,
                strip_command=False,
                strip_prompt=False,
            )
        logger.emit("Limpeza concluida.", "success")
    except Exception as e:
        logger.emit(f"Aviso: Falha ao limpar o arquivo temporario remoto: {e}", "warning")

    return (True, f"Backup do banco de dados '{db_name}' concluido!", local_filepath, "SUCESSO")
