# Go-Live Checklist

## 1) Segurança
- [ ] `APP_ENV=production` no `.env`
- [ ] `SECRET_KEY` forte e único (não usar default)
- [ ] `ENCRYPTION_KEY` válido e protegido
- [ ] HTTPS ativo e `SESSION_COOKIE_SECURE=true`
- [ ] Perfis e permissões validados (Admin Master/Admin/Técnico/Visualizador)

## 2) Banco e dados
- [ ] Backup do banco antes do deploy
- [ ] Migrações aplicadas e validadas
- [ ] Tenant piloto validado após migração
- [ ] Plano de rollback documentado e testado

## 3) Operação
- [ ] Containers `app`, `db`, `redis`, `worker`, `beat` em execução
- [ ] `/healthz` retornando `200`
- [ ] `/readyz` retornando `200` (DB + Redis)
- [ ] Rotina diária de backup executando no horário esperado

## 4) Qualidade
- [ ] Testes automatizados básicos passando
- [ ] Fluxos críticos testados manualmente:
  - [ ] Login/logout
  - [ ] Backup manual
  - [ ] Agendamento global
  - [ ] Comparação de backups
  - [ ] Gestão de usuários
  - [ ] Gestão de grupos/provedores

## 5) Comercial e suporte
- [ ] Planos e limites revisados
- [ ] Termos/privacidade (LGPD) publicados
- [ ] Canal de suporte e SLA definidos

## Comandos recomendados
```bash
python3 scripts/go_live_check.py
pytest -q tests/test_permissions_matrix.py
```
