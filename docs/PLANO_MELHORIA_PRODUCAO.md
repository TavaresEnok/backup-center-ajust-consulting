# Plano de melhoria — Backup Center (produção segura)

**Objetivo:** evoluir o sistema **sem quebrar produção**, com passos verificáveis, reversíveis e rastreáveis.

**Como usar:** marque `- [ ]` → `- [x]` ao concluir cada item. Não avance de fase sem fechar os **gates** indicados.

### Registo de execução (últimas alterações no repo)

- [x] **Fase 1 (parcial):** removidos segredos do `README.md` e `HANDOVER_FOR_AI.md`; `JUMP_HOST_SLOTS_OVERRIDES` e `FLOWER_*` externalizados via `.env` / `docker-compose`; criado `.env.example`.
- [x] **Fase 3 (parcial):** CI passa a executar `pytest` completo; corrigidos patches de schema em SQLite para testes (`is_sqlite_engine()`); ajuste de teste em `test_backup_diagnostics.py`.

**Regra de ouro:** *nunca* deploy direto em produção sem **backup verificado**, **janela** ou **canary** quando o risco for médio/alto.

---

## Legenda de risco por tarefa

| Símbolo | Significado |
|---------|-------------|
| 🔴 | Alto risco — exige janela, backup e plano de rollback |
| 🟡 | Risco médio — testar em staging primeiro |
| 🟢 | Baixo risco — pode fazer com cuidado em horário controlado |

---

## Fase 0 — Baseline e disciplina (antes de qualquer mudança estrutural)

> **Gate 0:** todas as caixas desta fase marcadas antes de iniciar Fase 1 (segurança sensível).

### 0.1 Inventário e congelamento

- [ ] Documentar **versão exata** em produção: commit Git, imagem Docker (tag/digest), `docker-compose` ativo.
- [ ] Listar **URLs**, **portas expostas** e **serviços** (app, Celery, Flower, Grafana, etc.).
- [ ] Definir **responsável** e **canal de comunicação** para deploy/incidente (ex.: grupo, ticket).
- [ ] Congelar alterações “grandes” até baseline da Fase 0 estar completa (combinar com a equipa).

### 0.2 Backup de dados e de configuração (obrigatório)

- [ ] **Snapshot / dump** PostgreSQL com procedimento testado de restore (restaurar numa BD vazia e validar).
- [ ] Backup do diretório **`storage/`** (artefactos de backup, ficheiros críticos).
- [ ] Exportar **`.env` de produção** para local seguro **fora** do Git (cofre, vault — não email em claro).
- [ ] Guardar cópia do **`docker-compose`** e overrides **reais** usados em produção.
- [ ] Registar **comando e hora** do último backup bem-sucedido: `________________________`

### 0.3 Ambiente espelho (recomendado antes de 🔴)

- [ ] Criar **staging** com mesmo `docker-compose` (ou quase), dados **anonimizados** ou subset.
- [ ] Validar `healthz` / `readyz` em staging após restore de backup de teste.
- [ ] Documentar diferenças aceitáveis prod vs staging (domínio, secrets, volumes).

### 0.4 Fluxo Git seguro

- [ ] Garantir que **produção** só recebe código de `main` (ou branch release) **após** merge e CI verde.
- [ ] Usar **tags** de release (ex.: `v1.2.3`) para cada deploy em produção.
- [ ] Proibir commit de `.env`, dumps, chaves privadas (confirmar `.gitignore`).

**Gate 0 concluído:** `[ ] Sim  [ ] Não` — Data: `_______` — Assinatura/equipa: `_______`

---

## Fase 1 — Segurança crítica (reduz risco de incidente imediato)

> **Gate 1:** após esta fase, não deve haver credenciais reais em ficheiros versionados; Flower não público sem auth.

### 1.1 Remover segredos do repositório 🔴

- [ ] Remover da documentação (`README.md`, `HANDOVER_FOR_AI.md`, outros) **todas** as passwords, IPs sensíveis e contas de exemplo reais.
- [ ] Substituir por placeholders e apontar para **cofre** / variáveis de ambiente.
- [ ] Se segredos **já** estiveram no Git: planear **rotação** (passwords, SSH, tokens Mercado Pago, `SECRET_KEY`, `ENCRYPTION_KEY`) e limpeza de histórico se a política de segurança exigir (`git filter-repo` / BFG) — **avaliar com cuidado** (rewrites afetam toda a equipa).
- [ ] Após rotação: validar login app, Celery, webhooks MP, API externa com tokens.

### 1.2 Flower e painéis internos 🟡

- [ ] Confirmar se **Flower** (`5555`) está acessível na Internet; se sim, **fechar** (firewall / bind 127.0.0.1) ou colocar **VPN**/reverse proxy com auth.
- [ ] Definir `FLOWER_UNAUTHENTICATED_API=false` (ou equivalente suportado) **após** confirmar modo de auth.
- [ ] Revisar **Prometheus/Grafana/Loki**: apenas rede interna ou auth forte; desativar anónimo em produção se aplicável.

### 1.3 Variáveis e compose em produção 🟡

- [ ] Mover **`JUMP_HOST_SLOTS_OVERRIDES`** e dados semelhantes para **env injetado** no deploy (não ficheiro no Git público).
- [ ] Validar `APP_ENV=production`, `DEBUG=false`, `SESSION_COOKIE_SECURE=true`, `AUTO_CREATE_SCHEMA=false` em produção.
- [ ] Confirmar `validate_settings()` a correr no arranque (falha rápido se config inválida).

### 1.4 Revisão de exposição

- [ ] Listar portas abertas no servidor (`ss`/`netstat`/cloud security group).
- [ ] Garantir HTTPS terminado corretamente e `X-Forwarded-Proto` coerente.

**Gate 1 concluído:** `[ ] Sim` — Data: `_______`

---

## Fase 2 — Base de dados e migrações (máximo cuidado)

> **Gate 2:** nenhuma migração em produção sem backup do dia e teste em staging.

### 2.1 Política de schema 🟡

- [ ] Inventariar tudo o que altera schema: **Alembic**, `ensure_schema()`, `Base.metadata.create_all`.
- [ ] Definir regra: **produção** = só **Alembic** (ou processo único documentado) — eliminar ambiguidade.
- [ ] Gerar revisões Alembic para diferenças atuais modelo ↔ BD real (autogenerate com revisão humana).

### 2.2 Procedimento de migração 🔴

- [ ] Escrever **runbook** em 1 página: backup → `alembic upgrade head` → smoke tests → monitorização 30 min.
- [ ] Executar **primeiro** em staging o mesmo `revision` e `upgrade`.
- [ ] Em produção: janela + responsável online + `alembic current` antes/depois registado.
- [ ] Plano **rollback**: `alembic downgrade -1` **só** se a revisão for reversível; caso contrário, preparar **forward fix**.

### 2.3 Integridade pós-migração

- [ ] Queries de contagem em tabelas críticas (`tenants`, `devices`, `backups`) antes/depois.
- [ ] Um login tenant + um job Celery de teste (ex.: backup dry-run ou dispositivo de lab).

**Gate 2 concluído:** `[ ] Sim` — Data: `_______`

---

## Fase 3 — Testes, CI e qualidade (reduz regressão)

> **Gate 3:** CI deve falhar se quebrar módulos críticos; coverage mínimo acordado.

### 3.1 Testes automatizados 🟢

- [ ] Expandir `pytest` para: **webhook Mercado Pago** (assinatura/erros), **rotas críticas** de tenant (permissão), **executor de backup** (mock de rede).
- [ ] Testes de **migração** em CI (BD limpa + upgrade head).
- [ ] Garantir que CI corre **toda** a suite relevante (não só um ficheiro), com tempo aceitável.

### 3.2 Smoke pós-deploy 🟢

- [ ] `curl` `/healthz` e `/readyz` no ambiente deployado.
- [ ] Login + abrir dashboard tenant de teste.
- [ ] Verificar worker Celery a consumir fila (Flower interno ou logs).

### 3.3 Observabilidade 🟢

- [ ] Alertas mínimos: app down, `readyz` falha DB/Redis, fila Celery crescendo sem consumo.
- [ ] Dashboard Grafana “overview” revisto com a equipa.

**Gate 3 concluído:** `[ ] Sim` — Data: `_______`

---

## Fase 4 — Operações e performance (sem mudar regra de negócio)

### 4.1 Armazenamento e retenção 🟡

- [ ] Dimensionar `storage/` — risco de disco cheio = falhas silenciosas.
- [ ] Política de retenção alinhada com produto (dias, legal) documentada.
- [ ] (Opcional) Plano para **object storage** S3-compat com migração faseada.

### 4.2 Celery e limites 🟢

- [ ] Revisar `concurrency`, timeouts e `max-tasks-per-child` vs carga real.
- [ ] Documentar o que fazer se **fila VPN** entupir (purge controlada, reinício worker).

### 4.3 Frontend / CDN 🟢

- [ ] Fixar versões de libs CDN (Alpine, Chart.js, Lucide) para evitar surpresas.
- [ ] Avaliar redução de `unsafe-eval` / inline (faseada, com testes visuais).

---

## Fase 5 — Produto e UX (após fundação estável)

> Só iniciar com **Gate 1–3** preferencialmente fechados.

### 5.1 Onboarding e NOC 🟢

- [ ] Wizard “primeiro dispositivo → teste de ligação → primeiro backup OK”.
- [ ] Bloco “Requer ação” no dashboard (credenciais, falhas recentes, atrasos).

### 5.2 API e integrações 🟡

- [ ] OpenAPI publicada para rotas **estáveis** da API externa.
- [ ] Webhooks de eventos (falha/sucesso de backup) com assinatura HMAC e retry.

### 5.3 Mobile / plantão 🟡

- [ ] PWA mínima ou página responsiva crítica (lista de falhas + detalhe).

---

## Plano de deploy seguro (checklist genérico — usar em cada release)

Copiar para cada release e marcar:

- [ ] **Backup** BD + `storage/` confirmado.
- [ ] **Staging** atualizado e smoke OK.
- [ ] **CHANGELOG** / notas de release escritas.
- [ ] **Migrações** aplicadas em staging; comando documentado para prod.
- [ ] **Feature flag** ou config nova com default seguro em prod.
- [ ] Deploy em **horário** acordado; responsável online.
- [ ] Pós-deploy: health, login, 1 fluxo crítico, logs Celery 15 min.
- [ ] Se algo falhar: **rollback** (imagem anterior + downgrade BD se aplicável) — runbook à mão.

**Release:** `_______` **Data:** `_______` **Responsável:** `_______`

---

## Rollback rápido (referência)

| Cenário | Ação imediata |
|---------|----------------|
| App não sobe | Reverter para imagem/commit anterior; verificar `readyz` |
| Erro pós-migração | Se reversível: `alembic downgrade`; senão: hotfix forward |
| Celery parado | Reiniciar worker; verificar Redis; filas |
| Disco cheio | Limpar logs antigos com critério; expandir volume; pausar jobs não críticos |

---

## Resumo das fases (ordem obrigatória sugerida)

1. **Fase 0** — Baseline + backups testados  
2. **Fase 1** — Segurança (segredos, Flower, compose)  
3. **Fase 2** — Migrações e schema único  
4. **Fase 3** — Testes + CI + smoke  
5. **Fase 4** — Ops (disco, filas, CDN)  
6. **Fase 5** — UX / API / integrações  

---

## Notas finais

- Este plano **não substitui** monitorização 24/7 nem seguro de dados; complementa.
- Qualquer item 🔴 em produção: **duas pessoas** a validar (quatro olhos) é boa prática.
- Atualizar este `.md` quando uma fase mudar (ex.: novo serviço no compose).

**Última revisão do documento:** _preencher na próxima edição_  
**Dono do plano:** _preencher_
