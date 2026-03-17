# Plano Ultra Detalhado - Backup Center

> Documento de planejamento abrangente (tecnico + produto + UX). Sem codigo.

## Status Atual (Implementado)
- RBAC unificado com `UserRole` nas rotas web (tenant e superadmin).
- CSRF aplicado em todas as rotas POST do Flask (token + validacao).
- Validacao de `SECRET_KEY`/`ENCRYPTION_KEY` e config de session cookies.
- Base de migrations (Alembic) adicionada ao projeto, incluindo migration de indices.
- Politica de retencao com job diario via Celery.
- Padronizacao da execucao de scripts (parametros, retorno, hash/size).
- Logging basico com formato JSON configuravel.
- Indices adicionados nos modelos de dados mais acessados.

## 0) Visao Executiva
- Objetivo: transformar o Backup Center em uma plataforma multi-tenant robusta, segura e escalavel para backup/monitoramento de dispositivos, com operacao previsivel e UX consistente.
- Premissas:
  - Stack atual: FastAPI + Flask (SSR), SQLAlchemy, Celery, Redis, Postgres.
  - Execucao local e scripts legados para backup.
  - Uso multi-tenant com roles basicos.
- Resultado esperado: reduzir riscos de seguranca, padronizar arquitetura, aumentar confiabilidade das tarefas e elevar a experiencia de uso.

## 1) Diagnostico do Estado Atual (Baseline)
### 1.1 Inventario tecnico
- Mapear entrypoints, services, tasks, modelos, rotas e templates.
- Catalogar dependencias criticas (psycopg2, cryptography, passlib, celery, redis, netmiko).
- Identificar componentes legados (scripts em `app/scripts/backup_scripts`).

### 1.2 Fluxos principais atuais
- Autenticacao: login/registro, sessao, roles.
- Operacao: cadastrar dispositivos, executar backups, monitorar status, relatorios.
- Admin: superadmin (plans, device types, tenants).

### 1.3 Riscos e gargalos iniciais
- Inconsistencias de controle de acesso (strings de role divergentes, pass silencioso). (feito)
- Ambientes conflitantes (Postgres vs SQLite e portas diferentes). (parcial)
- Retencao e armazenamento sem politica. (feito)
- Logs e observabilidade nao padronizados. (parcial)

**Entregaveis:**
- Mapa de arquitetura atual
- Diagrama de fluxo (auth, backup, monitoring, reports)
- Lista de riscos e debt tecnico

## 2) Arquitetura Alvo e Evolucao
### 2.1 Decisao macro
- Opcao A: manter monolito hibrido (FastAPI + Flask) com separacao clara.
- Opcao B: migrar para API-first (FastAPI) e front separado.
- Definir criterios: time, custo, risco, impacto em clientes.

### 2.2 Separacao de camadas
- Presentacao: templates e layout unificados.
- API: endpoints REST reais para recursos core.
- Dominio: services isolados por responsabilidade.
- Infra: tasks, filas, storage, observabilidade.

### 2.3 Modularizacao
- Pastas por dominio: auth, devices, backups, billing, reports.
- Evitar dependencias cruzadas entre camadas (web -> service -> models).

**Entregaveis:**
- Documento de arquitetura alvo
- Decisao tecnica registrada (ADR)
- Plano de migracao incremental

## 3) Seguranca e Conformidade
### 3.1 Autenticacao
- Definir padrao de sessao (duracao, renovacao, idle timeout). (parcial)
- Consistencia em roles (usar enum do modelo em todo lugar). (feito)
- Login rate-limiting e bloqueio por tentativas. (pendente)

### 3.2 Autorizacao (RBAC)
- Matriz de permissoes por role:
  - super_admin, tenant_owner, tenant_admin, technician, viewer.
- Middleware para checagens padronizadas. (feito)
- Log de auditoria para acoes criticas. (parcial)

### 3.3 Protecao contra CSRF
- Adicionar tokens CSRF em todas rotas POST do Flask. (feito)
- Padronizar forms com hidden input. (feito)

### 3.4 Criptografia e segredos
- Chave Fernet valida e rotacionavel. (feito)
- Segredos via env vars; remover defaults inseguros. (parcial)
- Politica de rotacao e backup de chaves. (pendente)

### 3.5 Isolamento multi-tenant
- Validacao de tenant em todas rotas sensiveis. (feito)
- Filtros de tenant em queries por padrao. (pendente)

**Entregaveis:**
- Matriz de permissoes
- Checklist de seguranca
- Documento de rotacao de chaves

## 4) Configuracao e Ambientes
### 4.1 Padronizacao de banco
- Definir Postgres como default. (feito)
- Unificar portas/configs (5432 vs 5433). (feito)
- Remover SQLite do fluxo principal (ou isolar para dev). (parcial)

### 4.2 Configuracao via .env
- Normalizar variaveis: DB, Redis, SMTP, secrets. (feito)
- Validacao no startup (falhar rapido se faltar env). (parcial)

### 4.3 Migrations
- Introduzir Alembic para evolucao de schema. (feito)
- Criar migrations iniciais e seeds. (pendente)

**Entregaveis:**
- Template .env
- Guia de setup local
- Migracoes iniciais

## 5) Modelos e Dados
### 5.1 Integridade
- Garantir constraints e foreign keys consistentes. (pendente)
- Revisar campos nullable e defaults. (pendente)

### 5.2 Indices
- Indices por tenant_id, device_id, created_at (backups). (feito)
- Indices para filtros frequentes (status, schedule, role). (feito)

### 5.3 Retencao e versionamento
- Politicas por plano: dias de retencao. (feito)
- Job de limpeza e arquivamento. (feito)

### 5.4 Storage
- Abstracao para storage (local, S3, NFS). (pendente)
- Metadados no banco para auditabilidade. (parcial)

**Entregaveis:**
- Lista de ajustes no schema
- Politica de retencao por plano
- Spec de storage

## 6) Backups e Execucao de Scripts
### 6.1 Padronizacao de scripts
- Definir interface unica (funcao realizar_backup). (parcial)
- Padronizar logs e outputs. (parcial)

### 6.2 Gerenciamento de falhas
- Retentativas com backoff. (pendente)
- Timeout por tipo de dispositivo. (pendente)
- Circuit breaker para dispositivos instaveis. (pendente)

### 6.3 Estrategia de backup
- Backup full + incrementais (se aplicavel). (pendente)
- Hash de arquivo e integridade. (feito)
- Restore basico (download + validacao). (pendente)

**Entregaveis:**
- Interface padrao de script
- Politica de retries
- Plano de restore

## 7) Tarefas e Assincronia (Celery)
### 7.1 Idempotencia
- Garantir que tasks possam ser reexecutadas sem dano. (pendente)

### 7.2 Observabilidade
- Log por task_id e device_id. (parcial)
- Metricas por fila (sucesso, falha, duracao). (pendente)

### 7.3 Orquestracao
- Separar filas por tipo (backups, monitoring, reports). (pendente)
- Definir concurrency e limites por tenant. (pendente)

**Entregaveis:**
- Config de filas
- Guidelines de idempotencia
- Mapa de tasks

## 8) Front-end e UX
### 8.1 Design system
- Definir tokens (cores, tipografia, spacing). (pendente)
- Componentes base (buttons, cards, tables, forms). (pendente)

### 8.2 Layout
- Navbar/Sidebar consistentes. (pendente)
- Breadcrumbs e estados ativos. (pendente)

### 8.3 UX flows
- Estados vazios, erros, loading. (pendente)
- Feedback claro em acoes criticas (backup, delete). (pendente)

### 8.4 Responsividade
- Layout mobile-first para dashboards e listas. (pendente)

**Entregaveis:**
- Guia visual (style guide)
- Component library basica
- Mockups principais

## 9) Funcionalidades Faltantes (Produto)
### 9.1 Core
- Restore de backup. (pendente)
- Notificacoes reais (email/alertas). (pendente)
- Relatorios exportaveis (PDF/CSV). (pendente)
- Politica de retencao por plano. (feito)

### 9.2 Billing
- Integracao com gateway (Stripe/Asaas). (pendente)
- Ciclo completo: trial, pagamento, cancelamento, renovacao. (pendente)

### 9.3 Monitoramento
- Alertas de dispositivos offline. (pendente)
- SLA e historico de disponibilidade. (pendente)

### 9.4 API
- CRUD completo (devices, backups, reports, billing). (pendente)
- Autenticacao por token. (pendente)

**Entregaveis:**
- Backlog funcional por prioridade
- Especificacao de API v1

## 10) Observabilidade
### 10.1 Logs
- Padrao estruturado (JSON) com correlacao. (parcial)
- Integracao com ELK/Grafana Loki. (pendente)

### 10.2 Metricas
- Prometheus: backups/duracao/falhas. (pendente)
- Dashboards Grafana. (pendente)

### 10.3 Alertas
- Notificar falhas criticas em backups. (pendente)
- SLA por tenant. (pendente)

**Entregaveis:**
- Stack de observabilidade
- Dashboards base

## 11) Testes e Qualidade
### 11.1 Unitarios
- Services core (auth, backup, monitoring). (pendente)

### 11.2 Integracao
- DB + API + tasks. (pendente)

### 11.3 E2E
- Fluxos principais no front (login, backup, report). (pendente)

### 11.4 CI/CD
- Pipeline com lint, tests, build. (pendente)

**Entregaveis:**
- Suite minima de testes
- Coverage target
- Pipeline CI

## 12) Roadmap e Fases
### Fase 1: Estabilizacao e Seguranca (2-4 semanas)
- Corrigir RBAC e CSRF. (feito)
- Normalizar DB/config. (feito)
- Logs estruturados basicos. (parcial)

### Fase 2: Arquitetura e Dados (4-6 semanas)
- Migracoes Alembic. (feito)
- Retencao e storage. (parcial)
- Padronizar scripts. (parcial)

### Fase 3: Produto e UX (6-8 semanas)
- Design system + refator de templates. (pendente)
- Relatorios exportaveis. (pendente)
- Notificacoes reais. (pendente)

### Fase 4: Observabilidade e API (4-6 semanas)
- API v1 completa. (pendente)
- Dashboards + alertas. (pendente)

**Entregaveis:**
- Roadmap detalhado com milestones
- Cronograma com dependencias

## 13) Riscos e Mitigacoes
- Risco: scripts legados instaveis -> Mitigacao: sandbox + testes por vendor.
- Risco: migracao de DB -> Mitigacao: backup, dry-run, rollback.
- Risco: mudanca de UX -> Mitigacao: rollout gradual.

## 14) Criterios de Aceitacao (MVP 2.0)
- 100% das rotas protegidas por RBAC e CSRF.
- Backups com retencao automatica e restore basico.
- Observabilidade minima ativa.
- UI consistente em todas as telas principais.
- Tests cobrindo fluxos criticos.

---

## Proximos Passos Recomendados
1. Validar arquitetura alvo (monolito vs API-first).
2. Definir prioridades com stakeholders.
3. Aprovar roadmap e iniciar fase 2.

