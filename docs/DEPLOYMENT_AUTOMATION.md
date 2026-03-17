# Deploy Automation (GitHub Actions)

## Workflows criados
- `CI`: `.github/workflows/ci.yml`
- `Deploy manual`: `.github/workflows/deploy.yml`
- `Rollback manual`: `.github/workflows/rollback.yml`

## Pré-requisitos
1. Repositório no GitHub.
2. Ambiente remoto com Docker Compose e repositório clonado.
3. Definir environments no GitHub:
   - `staging`
   - `production`
4. Habilitar **required reviewers** no environment `production` para aprovação manual.

## Secrets por Environment
Configure estes secrets em **Settings > Environments > staging/production**:

- `DEPLOY_HOST` (IP/DNS do servidor)
- `DEPLOY_PORT` (normalmente `22`)
- `DEPLOY_USER` (usuário SSH)
- `DEPLOY_SSH_KEY` (chave privada SSH)
- `DEPLOY_PATH` (ex: `/home/app/projects/backup_center`)
- `DEPLOY_URL` (ex: `http://168.194.13.18:8050`)

## Fluxo de deploy
1. Acesse **Actions > Deploy**.
2. Clique em **Run workflow**.
3. Selecione:
   - `environment`: `staging` ou `production`
   - `ref`: branch/tag (ex: `main`)
   - `run_migrations`: `true/false`
4. Aguarde a aprovação (se `production` exigir).

### Comportamento
- Faz `git fetch`, `checkout` e `pull`.
- Executa `docker compose up -d --build` dos serviços principais.
- Opcionalmente executa `alembic upgrade head`.
- Valida `healthz` e `readyz`.
- Se health falhar, executa rollback automático para commit anterior.

## Fluxo de rollback
1. Acesse **Actions > Rollback**.
2. Informe o commit alvo.
3. (Opcional) `run_migrations=true` para `alembic downgrade -1`.

## Observações
- Rollback de migração é simplificado para `-1`; use com cuidado em produção.
- Para bancos críticos, prefira rollback com backup snapshot + migração planejada.
