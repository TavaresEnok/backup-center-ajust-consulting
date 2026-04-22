# Backup Center

Sistema de gerenciamento de backups de equipamentos de rede para provedores de internet (ISPs).

## Stack tecnológica

- **Backend:** Python (FastAPI + Flask), SQLAlchemy, Celery
- **Base de dados:** PostgreSQL
- **Cache / broker:** Redis
- **Frontend:** Jinja2 + TailwindCSS

## Estrutura do projeto

```
backup_center_new/
├── app/
│   ├── core/          # Configurações, database, security
│   ├── models/        # Modelos SQLAlchemy
│   ├── services/      # Lógica de negócio
│   ├── web/           # Rotas Flask (UI)
│   ├── api/           # Rotas FastAPI (health, API externa)
│   ├── templates/     # Templates Jinja2
│   └── scripts/       # Scripts de backup por fabricante
├── docs/              # Documentação (incl. plano de melhoria em produção)
├── HANDOVER_FOR_AI.md # Contexto operacional (sem credenciais)
├── alembic/           # Migrações
└── docker-compose.yml
```

## Configuração

1. Copie variáveis de ambiente: `cp .env.example .env` e preencha **todos** os valores (nunca commite `.env`).
2. Em **produção**: use `APP_ENV=production`, `SECRET_KEY` e `ENCRYPTION_KEY` fortes e únicos, cookies seguros conforme `app/core/config.py`.

**Não** documente passwords, chaves SSH ou IPs internos neste repositório.

## Comandos úteis (Docker)

```bash
# Logs da aplicação
docker logs -f backup_sys_app

# Reiniciar aplicação
docker restart backup_sys_app

# Shell no container
docker exec -it backup_sys_app /bin/bash

# Health checks (ajuste host/porta conforme o seu deploy)
curl -s http://127.0.0.1:8050/healthz
curl -s http://127.0.0.1:8050/readyz
```

## Testes locais

```bash
pip install -r requirements.txt
export APP_ENV=development
export SECRET_KEY=dev-only
export ENCRYPTION_KEY=FbfA95Ns7rrcSJHNpXGLSWNH1jFB1FU6Bxlk-UiWEyI=
export DATABASE_URL=sqlite:///./local_test.db
export REDIS_URL=redis://localhost:6379/0
pytest -q
```

## Documentação

- Plano de evolução segura em produção: `docs/PLANO_MELHORIA_PRODUCAO.md`
- Contexto de sistema (sem segredos): `HANDOVER_FOR_AI.md`

## CI/CD

- CI: `.github/workflows/ci.yml`
- Deploy: `.github/workflows/deploy.yml`
- Rollback: `.github/workflows/rollback.yml`
- Guia (se existir): `docs/DEPLOYMENT_AUTOMATION.md`
