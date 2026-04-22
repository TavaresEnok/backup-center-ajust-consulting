# Backup Center — contexto para equipa e IA

**Tipo:** SaaS multi-tenant de backup de equipamentos de rede (foco ISP).  
**Não é:** ERP de provedor (sem faturação do assinante final, RADIUS, etc.).

## Stack

- **ASGI:** FastAPI (`/healthz`, `/readyz`, API v1)
- **UI:** Flask + Jinja2 + TailwindCSS (+ Alpine.js nos templates)
- **Dados:** PostgreSQL, Redis
- **Tarefas:** Celery (fila geral + `vpn_queue`), Celery Beat
- **Deploy típico:** Docker Compose (ver `docker-compose.yml`)

## Estrutura lógica

- **Tenant:** dispositivos, grupos, agendamentos, backups, relatórios, definições, tokens de API
- **Superadmin:** tenants, planos, billing, tipos de dispositivo
- **Scripts:** `app/scripts/backup_scripts/` — um ou mais por fabricante (SSH/netmiko, etc.)

## Documentação sensível

- **Credenciais, IPs internos e passwords** devem estar apenas em **variáveis de ambiente** ou cofre — **não** neste ficheiro nem no `README`.
- Produção: `validate_settings()` em `app/core/config.py` exige `SECRET_KEY` e `ENCRYPTION_KEY` adequados quando `APP_ENV` não é `development`.

## Histórico técnico (referência)

### Erro de enum no dashboard (resolvido)

- **Problema:** `BackupStatus` — inconsistência nome vs valor na BD.
- **Correção:** `app/models/backup.py` com `values_callable` no enum.

### `device_types` vazio

- Se contadores zerados, garantir seed/população de tipos de dispositivo conforme procedimento interno (não versionar passwords aqui).

## Desenvolvimento

- Alterações em código montado por volume: reiniciar serviço da app/workers conforme necessário.
- Migrações: preferir **Alembic**; evitar depender só de `create_all` em produção.
- Ao alterar interações com backups/enums, rever `app/models/backup.py`.

## Ficheiros úteis

- Plano de melhoria com checklist: `docs/PLANO_MELHORIA_PRODUCAO.md`
- Configuração: `app/core/config.py`
- Bootstrap app: `app/__init__.py`, `main.py`

---
*Mantenha este documento livre de segredos. Última revisão estrutural: alinhamento com repositório e segurança.*
