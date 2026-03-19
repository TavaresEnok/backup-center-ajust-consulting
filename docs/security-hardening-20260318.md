# Security Hardening — 2026-03-18

## Objetivo
Reduzir superfície de ataque imediata no ambiente do Backup Center (tenant e admin), priorizando isolamento de serviços internos e registro das mudanças.

## Mudanças aplicadas
- Banco PostgreSQL e Redis deixaram de expor portas públicas (5436, 6383). Agora acessíveis apenas pela rede interna Docker (`backup_net`).
- Aplicação web bindada somente em `127.0.0.1:5000` (acesso público deve vir via nginx/proxy com TLS).
- Worker `celery_vpn` deixou de usar `network_mode: host` e privilégios elevados. Agora usa rede interna, conecta em `db:5432` e `redis:6379`, mantém apenas `NET_ADMIN` para VPN, removido `privileged`/`SYS_ADMIN`/`apparmor:unconfined`.
- Documentação deste hardening criada para rastreabilidade.
- Segredos rotacionados (.env): `SECRET_KEY`, `DB_PASSWORD`, `REDIS_PASSWORD`, `MERCADO_PAGO_WEBHOOK_TOKEN`. Banco teve senha aplicada via `ALTER USER backup_user WITH PASSWORD '...'`.
- Serviços recompostos com `docker-compose up -d --build` para aplicar novas variáveis/segurança e confirmar saúde dos containers.
- Cabeçalhos de segurança adicionados globalmente (CSP, HSTS em HTTPS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy) em Flask/FastAPI. Redireciono para HTTPS em produção.
- Proteção extra: bloqueio de uso da ENCRYPTION_KEY default em produção; forço defaults seguros (DEBUG off, SESSION_COOKIE_SECURE on) quando APP_ENV != development.
- Entrada unificada no Apache (`backupcenter.ajustconsulting.com.br`) com proxy reverso para `127.0.0.1:5000`; site default desabilitado para evitar rota paralela.
- Serviços legados removidos da execução/boot (`mikrotik-legacy-gunicorn`, `mikrotik-legacy-celery`, `mikrotik-legacy-celery-vpn`, `mikrotik-legacy-flower`).
- Firewall ativo com política de negação por padrão (UFW): libera apenas `24365/tcp` (SSH), `80/tcp` e `443/tcp`.
- Segredos operacionais movidos para fora do projeto em arquivo root-only: `/etc/backup_center/secrets.env` (permissão `600`). O `.env` do projeto agora é link para esse caminho.
- Observabilidade crítica instalada com watchdog automatizado:
  - script: `scripts/backup_center_watchdog.sh` (instalado em `/usr/local/sbin/backup_center_watchdog.sh`)
  - timer systemd: `backup-center-watchdog.timer` (1 minuto)
  - alertas em `logger` + arquivo `/var/log/backup_center/critical_alerts.log`
  - monitora: erro 502 no proxy, queda de workers/app, falha de lote e erro de webhook de pagamento.
- Smoke check da fase crítica adicionado em `scripts/critical_phase_smoke_check.sh` para validar em um comando:
  - proxy + health + containers + portas + firewall + legado + watchdog.
- Hardening adicional de autenticacao (2026-03-19):
  - login com lockout por IP e por email (janela + bloqueio temporario), com Redis e fallback local;
  - rate-limit no fluxo `esqueci senha` por IP;
  - log de seguranca para lockout/rate-limit e aviso de conta sem 2FA configurado.
- Watchdog atualizado para alertar anomalias de autenticacao:
  - lockout/rate-limit de login/forgot-password;
  - login de conta sem 2FA.
- Fase 2 (2026-03-19) aplicada:
  - 2FA obrigatorio para todos os perfis de usuario no login (admin e tenant);
  - fluxo de setup inicial (`/auth/2fa/setup`) e validacao (`/auth/2fa/verify`);
  - sessao de login pendente com TTL e amarrada ao IP de origem;
  - cancelamento explicito da autenticacao pendente (`/auth/2fa/cancel`).
- Watchdog com alerta externo opcional:
  - script passa a ler configuracao em `/etc/backup_center/watchdog.env`;
  - suporta `ALERT_WEBHOOK_URL` e `ALERT_WEBHOOK_TOKEN` para envio HTTP POST dos alertas criticos.

## Riscos residuais e próximos passos recomendados
- Rotacionar periodicamente os segredos de produção (`SECRET_KEY`, `ENCRYPTION_KEY`, senhas DB/Redis/MercadoPago). Atenção: trocar `ENCRYPTION_KEY` requer recriptografar credenciais já salvas.
- Garantir listener TLS real em `443` no host (ou proxy externo oficial) para evitar depender apenas de redirecionamento.
- Adicionar cabeçalhos de segurança (CSP, HSTS, X-Frame-Options, Referrer-Policy) no proxy frontal.
- Implementar rate limiting/bloqueio de login e auditoria centralizada de logs.
- Revisar permissões de storage e scripts operacionais em produção.
- Validar se o worker VPN continua operando com apenas `NET_ADMIN`; se precisar de mais capacidades, reavaliar escopo mínimo em vez de `privileged`.

## Rollback (se necessário)
- Reverter commit `security hardening 2026-03-18` ou restaurar `docker-compose.yml` anterior.
- Reexpor portas: adicionar novamente os mapeamentos `5436:5432` e `6383:6379` (não recomendado).
- Reaplicar `network_mode: host`/`privileged` no `celery_vpn` se o fluxo de VPN parar (avaliar antes).
- Reativar serviços legados, se necessário: `systemctl enable --now mikrotik-legacy-gunicorn mikrotik-legacy-celery mikrotik-legacy-celery-vpn mikrotik-legacy-flower`.
- Desativar firewall emergencialmente: `/usr/sbin/ufw disable`.
- Desativar watchdog: `systemctl disable --now backup-center-watchdog.timer`.

## Verificação rápida (operacional)
- Executar pós-alteração:
  - `ROOT_PASS='***' ./scripts/critical_phase_smoke_check.sh`
- Resultado esperado: todos os checks `[OK]` e final `smoke check da fase critica concluido`.

## Teste funcional de auth (manual)
- Tentar 9 logins errados no mesmo email para validar lockout por conta.
- Tentar alta taxa de `esqueci senha` do mesmo IP para validar rate-limit.
- Verificar alertas em `/var/log/backup_center/critical_alerts.log` e `journalctl -t backup-center-alert`.
- Para alerta externo, criar `/etc/backup_center/watchdog.env` com permissao `600`:
  - `ALERT_WEBHOOK_URL=https://...`
  - `ALERT_WEBHOOK_TOKEN=...` (opcional).
