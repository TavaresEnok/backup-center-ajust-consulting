"""
Backup Executor Service

Este serviÃ§o executa backups usando os scripts especializados do sistema legado.
Cada tipo de equipamento tem seu prÃ³prio script com a lÃ³gica de conexÃ£o e coleta.
"""

import os
import sys
import importlib.util
import hashlib
import logging
from datetime import datetime
from typing import Optional, Dict, Any, Tuple

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import decrypt_password
from app.models import Device, DeviceType, DeviceGroup, Backup, BackupStatus, Notification, NotificationType, User, UserRole
from app.services.vpn_service import vpn_service, VpnError
from app.services.realtime_backup_logs import append_task_log
from app.services.backup_diagnostics import (
    classify_failure,
    failure_label,
    validate_backup_integrity,
)


class BackupLogger:
    """Logger centralizado para operaÃ§Ãµes de backup."""
    
    def __init__(self, device_name: str, verbose: bool = True, task_id: Optional[str] = None):
        self.device_name = device_name
        self.verbose = verbose
        self.task_id = task_id
        self.logs = []
    
    def log(self, message: str, level: str = 'info'):
        timestamp = datetime.now().strftime('%H:%M:%S')
        log_entry = f"[{timestamp}] [{level.upper()}] [{self.device_name}] {message}"
        self.logs.append({'level': level, 'message': message, 'timestamp': timestamp})
        if self.verbose:
            logger = logging.getLogger(__name__)
            level_map = {
                'info': logging.INFO,
                'success': logging.INFO,
                'warning': logging.WARNING,
                'error': logging.ERROR,
            }
            logger.log(level_map.get(level, logging.INFO), log_entry)
        if self.task_id:
            append_task_log(self.task_id, self.device_name, message, level)
    
    def info(self, message: str):
        self.log(message, 'info')
    
    def success(self, message: str):
        self.log(message, 'success')
    
    def error(self, message: str):
        self.log(message, 'error')
    
    def warning(self, message: str):
        self.log(message, 'warning')


class BackupExecutor:
    """
    Executa backups usando os scripts especializados.
    """
    
    SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'backup_scripts')
    BACKUP_BASE_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'storage', 'backups')
    
    def __init__(self):
        self._script_cache = {}
        self._script_load_errors = {}
    
    def _load_script(self, script_name: str) -> Optional[Any]:
        """
        Carrega dinamicamente um script de backup.
        """
        if script_name in self._script_cache:
            return self._script_cache[script_name]
        
        # Garantir que imports absolutos (ex: import script_helpers) funcionem
        if self.SCRIPTS_DIR not in sys.path:
            sys.path.append(self.SCRIPTS_DIR)
        
        script_path = os.path.join(self.SCRIPTS_DIR, script_name)
        
        if not os.path.exists(script_path):
            self._script_load_errors[script_name] = "Arquivo nao encontrado."
            return None
        
        try:
            spec = importlib.util.spec_from_file_location(script_name.replace('.py', ''), script_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._script_cache[script_name] = module
            self._script_load_errors.pop(script_name, None)
            return module
        except Exception as exc:
            self._script_load_errors[script_name] = str(exc)
            logging.getLogger(__name__).exception("failed to load script %s", script_name)
            return None
    
    def _get_backup_path(self, tenant_slug: str, group_name: str, device_name: str) -> str:
        """
        Retorna o caminho do diretÃ³rio de backup para um dispositivo.
        Estrutura: storage/backups/{tenant_slug}/{group_name}/{device_name}/
        """
        # Sanitiza nomes para uso em paths
        def sanitize(name: str) -> str:
            if not name:
                return "unnamed"
            return "".join(c for c in name if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
        
        path = os.path.join(
            self.BACKUP_BASE_DIR,
            sanitize(tenant_slug),
            sanitize(group_name),
            sanitize(device_name)
        )
        os.makedirs(path, exist_ok=True)
        return path
    
    def execute_backup(
        self,
        device: Device,
        device_type: DeviceType,
        group: Optional[DeviceGroup] = None,
        tenant_slug: str = "default",
        manage_vpn: bool = True,
        task_id: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Executa backup de um dispositivo.
        
        Returns:
            Tuple[bool, str, Optional[str]]: (sucesso, mensagem, caminho_arquivo)
        """
        logger = BackupLogger(device.name, task_id=task_id)
        
        # Verifica se tem script configurado
        if not device_type or not device_type.script_name:
            return False, "Tipo de dispositivo sem script configurado", None
        
        # Carrega o script
        script = self._load_script(device_type.script_name)
        if not script:
            load_error = self._script_load_errors.get(device_type.script_name)
            if load_error:
                return False, f"Falha ao carregar script {device_type.script_name}: {load_error}", None
            return False, f"Script {device_type.script_name} nao encontrado", None
        
        # Verifica se o script tem a funcao realizar_backup
        if not hasattr(script, 'realizar_backup'):
            return False, f"Script {device_type.script_name} nao tem funcao realizar_backup", None
        
        # Prepara argumentos
        group_name = group.name if group else "Sem Grupo"
        backup_dir = self._get_backup_path(tenant_slug, group_name, device.name)
        
        # Descriptografa senha
        password = decrypt_password(device.password_encrypted)
        parametros = {}
        if device.extra_parameters:
            parametros.update(device.extra_parameters)
        parametros.setdefault('password', password)
        # Compatibilidade com scripts legados que leem a flag dentro de `parametros`.
        parametros['use_telnet'] = bool(device.use_telnet)
        
        # Monta argumentos para o script
        kwargs = {
            'ip': device.ip_address,
            'porta': device.port,
            'usuario': device.username,
            'password': password,
            'nome_provedor': group_name,
            'nome_tipo_equip': device_type.name,
            'nome_dispositivo': device.name,
            'backup_dir': backup_dir,
            'backup_base_path': backup_dir,
            'parametros': parametros,
            'logger': logger,
            'task_id': task_id,
        }
        
        # Adiciona parametros extras se existirem
        if device.extra_parameters:
            kwargs.update(device.extra_parameters)
        
        # Adiciona flag de telnet se necessário
        if device.use_telnet:
            # Alguns scripts leem `use_telnet`, outros `usar_telnet`.
            kwargs['use_telnet'] = True
            kwargs['usar_telnet'] = True
        
        # Adiciona parâmetros de Jump Host se o grupo usar
        if group and group.uses_jump_host and group.jump_host:
            logger.info(f"Usando Jump Host: {group.jump_host}:{group.jump_port or 22}")
            jump_password = None
            jump_key = None
            if group.jump_password_encrypted:
                jump_password = decrypt_password(group.jump_password_encrypted)
            if group.jump_key_encrypted:
                jump_key = decrypt_password(group.jump_key_encrypted)
            
            kwargs['jump_host'] = {
                'host': group.jump_host,
                'port': group.jump_port or 22,
                'username': group.jump_username,
                'password': jump_password,
                'key': jump_key,
            }
            # Também adiciona como parâmetros individuais para compatibilidade
            kwargs['usar_jump_host'] = True
            kwargs['jump_host_ip'] = group.jump_host
            kwargs['jump_host_porta'] = group.jump_port or 22
            kwargs['jump_host_usuario'] = group.jump_username
            kwargs['jump_host_senha'] = jump_password
            kwargs['jump_host_chave'] = jump_key
        
        def _execute_script():
            logger.info(f"Iniciando backup...")
            logger.info(f"Tipo: {device_type.name}")
            logger.info(f"Script: {device_type.script_name}")
            
            try:
                # Executa o backup
                result = script.realizar_backup(**kwargs)

                success = None
                message = None
                explicit_path = None
                if isinstance(result, (tuple, list)):
                    if len(result) > 0:
                        success = bool(result[0])
                    if len(result) > 1:
                        message = result[1]
                    if len(result) > 2:
                        explicit_path = result[2]
                elif isinstance(result, bool):
                    success = result
                else:
                    success = bool(result)

                if success:
                    # Busca o arquivo mais recente no diretÇürio
                    file_path = explicit_path
                    if not file_path:
                        files = sorted(
                            [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if os.path.isfile(os.path.join(backup_dir, f))],
                            key=os.path.getmtime,
                            reverse=True
                        )
                        file_path = files[0] if files else None

                    if file_path:
                        logger.success("Backup realizado com sucesso!")
                        return True, message or "Backup realizado com sucesso", file_path
                    else:
                        msg = "Comando executado, mas nenhum arquivo gerado/encontrado."
                        logger.error(msg)
                        return False, msg, None
                else:
                    logger.error(message or "Falha no backup")
                    return False, message or "Falha ao realizar backup", None
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Erro: {error_msg}")
                return False, f"Erro durante backup: {error_msg}", None

        if group and group.uses_vpn and manage_vpn:
            try:
                with vpn_service.vpn_session(group, logger=logger):
                    return _execute_script()
            except VpnError as e:
                logger.error(str(e))
                return False, f"Falha ao preparar VPN: {e}", None

        return _execute_script()
    
    def run_backup_for_device_id(
        self,
        device_id: str,
        manage_vpn: bool = True,
        task_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Executa backup para um dispositivo pelo ID.
        """
        db = SessionLocal()
        
        try:
            device = db.query(Device).filter_by(id=device_id).first()
            if not device:
                return False, "Dispositivo nao encontrado"
            
            device_type = db.query(DeviceType).filter_by(id=device.device_type_id).first()
            group = db.query(DeviceGroup).filter_by(id=device.group_id).first() if device.group_id else None
            tenant_slug = device.tenant.slug if device.tenant else "default"
            
            started_at = datetime.utcnow()
            success, message, file_path = self.execute_backup(
                device=device,
                device_type=device_type,
                group=group,
                tenant_slug=tenant_slug,
                manage_vpn=manage_vpn,
                task_id=task_id,
            )
            completed_at = datetime.utcnow()
            
            file_size = None
            file_hash = None
            if file_path and os.path.exists(file_path):
                file_size = os.path.getsize(file_path)
                hasher = hashlib.sha256()
                with open(file_path, 'rb') as handle:
                    for chunk in iter(lambda: handle.read(8192), b''):
                        hasher.update(chunk)
                file_hash = hasher.hexdigest()

            integrity = None
            if success:
                integrity = validate_backup_integrity(
                    file_path=file_path,
                    device_type_name=(device_type.name if device_type else ""),
                    script_name=(device_type.script_name if device_type else ""),
                )
                if not integrity.get("ok"):
                    success = False
                    message = (
                        "Backup gerado, mas invalidado por integridade: "
                        f"{integrity.get('reason') or 'erro de validacao'}."
                    )

            failure_category = None
            failure_category_label = None
            if not success:
                failure_category = classify_failure(message)
                failure_category_label = failure_label(failure_category)

            backup_meta = {
                "meta": {
                    "device_type": device_type.name if device_type else None,
                    "script_name": device_type.script_name if device_type else None,
                    "failure_category": failure_category,
                    "failure_label": failure_category_label,
                    "integrity": integrity,
                }
            }

            # Cria registro de backup
            backup = Backup(
                device_id=device.id,
                status=BackupStatus.SUCCESS if success else BackupStatus.FAILED,
                error_message=None if success else message,
                config_data=backup_meta,
                file_path=file_path,
                file_size_bytes=file_size,
                hash_sha256=file_hash,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=max(0, int((completed_at - started_at).total_seconds())),
            )
            db.add(backup)

            # Atualiza device
            device.last_backup_at = datetime.utcnow()
            device.last_backup_status = 'success' if success else 'failure'
            extra = dict(device.extra_parameters or {})
            extra["last_backup_integrity_ok"] = bool(integrity.get("ok")) if integrity else bool(success)
            if integrity:
                extra["last_backup_integrity_reason"] = str(integrity.get("reason") or "")
            if success:
                extra.pop("last_backup_failure_category", None)
                extra.pop("last_backup_failure_label", None)
                extra.pop("last_backup_failure_message", None)
                extra.pop("last_backup_failure_at", None)
            else:
                extra["last_backup_failure_category"] = failure_category or "unknown"
                extra["last_backup_failure_label"] = failure_category_label or "Outros"
                extra["last_backup_failure_message"] = str(message or "")
                extra["last_backup_failure_at"] = completed_at.isoformat() + "Z"
            device.extra_parameters = extra
            
            db.commit()

            # Notificações não podem impedir o registro do backup.
            if not success and device.tenant_id:
                try:
                    recipients = db.query(User).filter(
                        User.tenant_id == device.tenant_id,
                        User.role.in_([UserRole.TENANT_OWNER, UserRole.TENANT_ADMIN])
                    ).all()
                    for recipient in recipients:
                        db.add(Notification(
                            user_id=recipient.id,
                            type=NotificationType.BACKUP_FAILED,
                            title="Backup falhou",
                            message=f"Dispositivo {device.name}: {message}",
                        ))
                    db.commit()
                except Exception:
                    db.rollback()
                    logging.getLogger(__name__).exception(
                        "Falha ao registrar notificacao de backup para o dispositivo %s",
                        device.id
                    )
            
            return success, message
            
        except Exception as e:
            db.rollback()
            return False, str(e)
        finally:
            db.close()


# Singleton
backup_executor = BackupExecutor()
