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

## Riscos residuais e próximos passos recomendados
- Rotacionar todos os segredos de produção (`SECRET_KEY`, `ENCRYPTION_KEY`, senhas DB/Redis/MercadoPago) fora do repositório; usar vault/variáveis de ambiente seguras. Atenção: trocar `ENCRYPTION_KEY` requer recriptografar credenciais já salvas.
- Garantir firewall bloqueando 5432/6379/5000 externamente; manter apenas nginx/https exposto.
- Adicionar cabeçalhos de segurança (CSP, HSTS, X-Frame-Options, Referrer-Policy) no proxy frontal.
- Implementar rate limiting/bloqueio de login e auditoria centralizada de logs.
- Revisar permissões de `.env` e storage em produção (usar 600, remover de git se ainda rastreado).
- Validar se o worker VPN continua operando com apenas `NET_ADMIN`; se precisar de mais capacidades, reavaliar escopo mínimo em vez de `privileged`.

## Rollback (se necessário)
- Reverter commit `security hardening 2026-03-18` ou restaurar `docker-compose.yml` anterior.
- Reexpor portas: adicionar novamente os mapeamentos `5436:5432` e `6383:6379` (não recomendado).
- Reaplicar `network_mode: host`/`privileged` no `celery_vpn` se o fluxo de VPN parar (avaliar antes).
