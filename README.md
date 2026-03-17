# Backup Center

Sistema de gerenciamento de backups para provedores de internet (ISPs).

## Stack Tecnológica
- **Backend:** Python (Flask), SQLAlchemy, Celery
- **Database:** PostgreSQL
- **Cache/Broker:** Redis
- **Frontend:** Jinja2 Templates + TailwindCSS

## Estrutura do Projeto
```
/home/backupp/backup_project/
├── app/
│   ├── core/          # Configurações, database, security
│   ├── models/        # SQLAlchemy models
│   ├── services/      # Lógica de negócio
│   ├── web/           # Rotas e controllers
│   ├── templates/     # Templates Jinja2
│   └── scripts/       # Scripts de backup
├── docs/              # Documentação
│   └── HANDOVER_FOR_AI.md  # Contexto completo para AI
└── docker-compose.yml
```

## Acessos
| Serviço | URL/Host | Credenciais |
|---------|----------|-------------|
| SSH | 168.194.13.17 | backupp / asdSD@91582685 |
| App (Tenant) | :8000 | audemario@ajustconsulting.com.br / 123456 |
| App (Admin) | :8000 | admin@backupcenter.com / 123456 |

## Comandos Úteis
```bash
# Ver logs da aplicação
docker logs -f backup_sys_app

# Reiniciar aplicação
docker restart backup_sys_app

# Acessar shell do container
docker exec -it backup_sys_app /bin/bash

# Health checks
curl -s http://127.0.0.1:8050/healthz
curl -s http://127.0.0.1:8050/readyz

# Auditoria de consistência (dashboard/dispositivos)
docker run --rm --network backup_net \
  -v /home/app/projects/backup_center:/work \
  -w /work --env-file /home/app/projects/backup_center/.env \
  python:3.11-slim bash -lc \
  "pip install -q -r requirements.txt && PYTHONPATH=/work python scripts/audit_dashboard_data.py --tenant-slug ajust-consulting --output pretty"
```

## Documentação Completa
Veja `docs/HANDOVER_FOR_AI.md` para contexto detalhado sobre arquitetura, correções aplicadas e próximos passos.

## CI/CD
- CI: `.github/workflows/ci.yml`
- Deploy manual com aprovação: `.github/workflows/deploy.yml`
- Rollback manual: `.github/workflows/rollback.yml`
- Guia: `docs/DEPLOYMENT_AUTOMATION.md`
