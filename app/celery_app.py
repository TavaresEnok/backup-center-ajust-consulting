"""
ConfiguraÃ§Ã£o do Celery para processamento assÃ­ncrono de tarefas.

Para iniciar o worker Celery:
    celery -A app.celery_app worker --loglevel=info

Para iniciar o beat scheduler (tarefas periÃ³dicas):
    celery -A app.celery_app beat --loglevel=info
"""

from celery import Celery
from celery.schedules import crontab
from kombu import Queue
import os
from app.core.logging_config import setup_logging

setup_logging()

# ConfiguraÃ§Ã£o do broker (Redis)
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
APP_TIMEZONE = os.environ.get('APP_TIMEZONE', 'America/Recife')

# Por padrao nao aplica hard time limit global.
# Alguns backups (especialmente grupos VPN) podem ultrapassar 5 minutos.
_task_time_limit_env = str(os.environ.get('CELERY_TASK_TIME_LIMIT', '')).strip()
_task_soft_time_limit_env = str(os.environ.get('CELERY_TASK_SOFT_TIME_LIMIT', '')).strip()

TASK_TIME_LIMIT = int(_task_time_limit_env) if _task_time_limit_env.isdigit() else None
TASK_SOFT_TIME_LIMIT = int(_task_soft_time_limit_env) if _task_soft_time_limit_env.isdigit() else None

# Cria instÃ¢ncia do Celery
celery_app = Celery(
    'backup_center',
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=['app.tasks.monitoring', 'app.tasks.reports', 'app.tasks.backups']
)

# ConfiguraÃ§Ãµes
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone=APP_TIMEZONE,
    enable_utc=True,
    task_track_started=True,
    task_time_limit=TASK_TIME_LIMIT,
    task_soft_time_limit=TASK_SOFT_TIME_LIMIT,
    worker_prefetch_multiplier=1,
    result_expires=3600,  # Resultados expiram em 1 hora
    task_default_queue='celery',
    task_queues=(
        Queue('celery'),
        Queue('vpn_queue'),
    ),
    task_routes={
        'app.tasks.backups.run_vpn_group_backups_task': {'queue': 'vpn_queue'},
    },
)

# Tarefas periÃ³dicas (Celery Beat)
celery_app.conf.beat_schedule = {
    # Executa backups agendados (por horario de cada dispositivo)
    'run-scheduled-backups-every-minute': {
        'task': 'app.tasks.backups.run_scheduled_backups',
        'schedule': crontab(minute='*'),
    },
    # Ping de todos os dispositivos a cada 5 minutos
    'ping-all-devices-every-5-min': {
        'task': 'app.tasks.monitoring.ping_all_devices_periodic',
        'schedule': crontab(minute='*/5'),
    },
    # Enviar relatÃ³rios diÃ¡rios Ã s 8h
    'send-daily-reports': {
        'task': 'app.tasks.reports.send_scheduled_reports',
        'schedule': crontab(hour=8, minute=0),
        'args': ('daily',)
    },
    # Enviar relatÃ³rios semanais Ã s segundas-feiras Ã s 9h
    'send-weekly-reports': {
        'task': 'app.tasks.reports.send_scheduled_reports',
        'schedule': crontab(hour=9, minute=0, day_of_week='monday'),
        'args': ('weekly',)
    },
    # Limpeza de backups expirados (diariamente 02:30)
    'purge-expired-backups': {
        'task': 'app.tasks.backups.purge_expired_backups',
        'schedule': crontab(hour=2, minute=30),
    },
}


# FunÃ§Ã£o para obter contexto Flask dentro das tasks
def get_flask_app():
    """Retorna a aplicaÃ§Ã£o Flask para uso dentro das tasks Celery."""
    from app import create_flask_app
    return create_flask_app()
