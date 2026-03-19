# Resiliencia e Governanca de Consumo por Plano

## Objetivo
Evitar que clientes com plano de baixo custo consumam recursos de alto custo (storage, banda, capacidade operacional), mantendo previsibilidade de custo do SaaS e estabilidade da plataforma.

## Escopo implementado (fase atual)

### 1. Limites de plano com enforcement real
- `max_devices`: bloqueia cadastro quando o tenant atinge o limite.
- `max_users`: bloqueia criacao/reativacao de usuario quando atinge o limite.
- `storage_quota_gb`: bloqueia persistencia de backup quando storage do tenant ultrapassa o limite.
- `download_quota_gb_month`: bloqueia download quando consumo mensal ultrapassa a cota.
- `max_download_rate_mbps`: aplica limitacao de taxa no stream de download de backup quando configurado (> 0).

### 2. Medicao de consumo
- Uso de storage: soma de `backups.file_size_bytes` por tenant.
- Uso de download mensal: tabela `tenant_usage_metrics`, chave por tenant + mes (`YYYY-MM`) + metrica.

### 3. Pontos de enforcement no fluxo
- Cadastro de dispositivo (tenant): validacao de limite de dispositivos.
- Cadastro/reativacao de usuarios (tenant e superadmin): validacao de limite de usuarios.
- Execucao de backup: validacao de storage antes de persistir; em excesso, o backup e marcado como falha e o arquivo gerado e removido.
- Download de backup: validacao/consumo da cota mensal e aplicacao opcional de throttle por plano.

### 4. UX e visibilidade
- Admin > Planos: novos campos para quotas e banda por plano.
- Cliente > Billing: exibicao de limites e consumo atual (storage e download mensal), com barra de progresso.
- Cliente > Dashboard: card de storage com limite e percentual.
- Troca de plano: validacao de elegibilidade com base em dispositivos, usuarios ativos e storage atual.

## Regras operacionais definidas
- `0` em quota de storage/download = ilimitado.
- Tenant com `access_unlimited=true` ignora quotas.
- Tenant sem plano e sem `access_unlimited` nao deve operar; fluxos sensiveis retornam erro de plano ausente.

## Riscos mitigados
- Crescimento descontrolado de armazenamento por cliente de ticket baixo.
- Esgotamento de banda por downloads excessivos.
- Upgrade/downgrade sem aderencia de capacidade real.

## Proxima fase recomendada (resiliencia)

### 1. Banco e Redis
- Replica de leitura PostgreSQL.
- Backup PITR (Point-In-Time Recovery) com retenção em objeto externo.
- Redis com persistencia AOF + replica.

### 2. Storage de backups
- Migrar arquivos para objeto (S3/MinIO) com versionamento.
- Lifecycle por plano (expurgo automatico e tiering).
- Checksums periodicos para deteccao de corrupcao.

### 3. Processamento
- Filas separadas por tier (premium x baseline).
- QoS por tenant (fair-share): evitar monopolio de workers.
- Circuit breaker para tenants em erro recorrente (evitar tempestade de retries).

### 4. Observabilidade e SLO
- Dashboards por tenant: storage, download mensal, falhas por categoria.
- Alertas preditivos (80%, 90%, 100% de consumo).
- SLOs: sucesso de backup, latencia de job, tempo de restauracao.

### 5. Governanca comercial
- Matriz de limites por plano (dispositivo, usuario, storage, download mensal, retenção).
- Add-ons de storage/download para upsell sem migração forçada de plano.
- Politica de grace period para excedente de cota antes de bloqueio duro.

## Checklist tecnico da fase atual
- [x] Schema de quotas em `plans`.
- [x] Tabela de metrica de consumo mensal.
- [x] Enforcement em backup e download.
- [x] Enforcement em dispositivos/usuarios.
- [x] Exibicao de consumo no billing e dashboard.
- [x] Validacao de elegibilidade na troca de plano.

