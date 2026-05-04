# Plano de Auditoria por Usuario (Sem Impacto em Producao)

## Objetivo
Implementar auditoria completa por usuario, com rastreabilidade de acoes e regra de acesso:
- cada usuario ve seus proprios logs;
- apenas Administrador Master pode ver logs de outros usuarios do tenant;
- Super Admin da plataforma mantem visao global.

## Estado atual verificado no sistema
Data da verificacao: 2026-05-04

- Ja existe tabela/modelo de atividade: `app/models/activity_log.py`.
- Ja existe servico de gravacao: `app/services/activity_service.py` (`log_action`).
- Ja existem varios pontos de gravacao nas rotas (login, dispositivos, grupos, agendamentos, etc.).
- Tela de atividade atual: `app/web/tenant/activity.py` + `app/templates/tenant/activity/list.html`.
- Gap principal: a tela lista logs do tenant sem filtro obrigatorio por usuario/permissao fina.

## Regras funcionais propostas
1. Visibilidade padrao:
- usuario comum (viewer/tecnico/admin) ve apenas seus logs (`ActivityLog.user_id == session.user_id`).
- Administrador Master (`TENANT_OWNER`) pode ver logs de qualquer usuario do tenant.
- Super Admin pode ver logs de qualquer tenant.

2. Escopo de consulta:
- sempre filtrar por `tenant_id` para evitar vazamento cross-tenant.
- para usuarios nao master, ignorar parametros de URL que tentem trocar `user_id`.

3. Auditoria dos acessos aos logs:
- registrar acao `VIEW_ACTIVITY_LOGS` com metadados (filtro usado, quantidade retornada, ip).

## Plano tecnico por fases (zero downtime)

### Fase 1 - Hardening da consulta (baixo risco)
- Ajustar `app/web/tenant/activity.py` para aplicar RBAC por usuario:
  - detectar perfil da sessao (`session['user_role']`);
  - se nao for `TENANT_OWNER` nem `SUPER_ADMIN`, filtrar por `session['user_id']`;
  - opcionalmente aceitar `?user_id=` apenas para `TENANT_OWNER`/`SUPER_ADMIN`.
- Adicionar validacao de UUID e fallback seguro.
- Nao alterar schema de banco nesta fase.

### Fase 2 - Enriquecimento dos eventos (baixo risco)
- Padronizar `details` em JSON serializado com chaves:
  - `resource_type`, `resource_id`, `result`, `message`, `request_id`.
- Incluir `request_id` por requisicao para rastreio ponta-a-ponta.
- Manter compatibilidade com logs antigos em texto.

### Fase 3 - Performance e retencao (controlado)
- Criar indice composto em migracao:
  - `(tenant_id, user_id, created_at DESC)`.
- Adicionar paginacao real na tela de atividade (cursor ou pagina/limite).
- Definir retencao (ex.: 180/365 dias) com job de limpeza fora de horario de pico.

### Fase 4 - Observabilidade e governanca
- Dashboard simples de auditoria (eventos por usuario, falhas, acessos suspeitos).
- Alertas para eventos sensiveis (`LOGIN_FAILED`, `DELETE_*`, `BACKUP_STOP_ALL`).
- Registro de acesso a logs de terceiros por Master Admin.

## Plano de deploy sem impacto
1. Criar feature flag: `AUDIT_USER_SCOPING_ENABLED=false` (default).
2. Publicar codigo com flag desligada (sem mudanca de comportamento).
3. Habilitar em homologacao e validar:
- usuario tecnico/admin so ve proprio log;
- tenant owner ve logs de todos do tenant;
- super admin mantem acesso global.
4. Habilitar em producao fora do horario de pico.
5. Monitorar por 24h: latencia da rota, erros 403 indevidos, volume de consultas.
6. Se necessario, rollback apenas desligando a flag.

## Testes obrigatorios
- Unitarios:
  - regra de permissao por role e escopo de tenant.
  - parse/validacao de `user_id` em filtro.
- Integracao:
  - cada role com sessao real acessando `/tenant/<slug>/activity`.
- Regressao:
  - verificar que logs continuam sendo gravados nas rotas atuais.

## Riscos e mitigacoes
- Risco: quebra de visibilidade para time operacional.
  - Mitigacao: rollout por flag + validacao com usuarios chave.
- Risco: consulta lenta com crescimento de logs.
  - Mitigacao: indice composto + paginacao.
- Risco: eventos antigos sem JSON estruturado.
  - Mitigacao: parser tolerante e exibicao com fallback texto.

## Checklist de execucao
- [ ] Implementar Fase 1 com flag.
- [ ] Criar testes RBAC da atividade.
- [ ] Homologar com perfis reais.
- [ ] Planejar migracao de indice em janela segura.
- [ ] Ativar em producao e monitorar.

