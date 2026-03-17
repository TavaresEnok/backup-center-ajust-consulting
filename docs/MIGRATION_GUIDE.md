# 📦 GUIA COMPLETO DE MIGRAÇÃO - BACKUP CENTER

> **Localização Atual:** `/home/backupp/backup_project`  
> **Servidor Atual:** `168.194.13.17`  
> **Data de Criação:** 30/01/2026  
> **Versão do Sistema:** 2.0 (Reescrito em Flask com Docker)

---

## 📋 ÍNDICE

1. [Visão Geral do Sistema](#visão-geral-do-sistema)
2. [Arquitetura e Stack Tecnológica](#arquitetura-e-stack-tecnológica)
3. [Portas Utilizadas](#portas-utilizadas)
4. [Credenciais e Acessos](#credenciais-e-acessos)
5. [Configuração Docker](#configuração-docker)
6. [Banco de Dados](#banco-de-dados)
7. [Estrutura de Arquivos](#estrutura-de-arquivos)
8. [Variáveis de Ambiente](#variáveis-de-ambiente)
9. [Processo de Migração](#processo-de-migração)
10. [Comandos Úteis](#comandos-úteis)
11. [Troubleshooting](#troubleshooting)

---

## 🎯 VISÃO GERAL DO SISTEMA

### O que é o Backup Center?

Sistema de gerenciamento automatizado de backups de equipamentos de rede para provedores de internet (ISPs). Suporta múltiplos fabricantes e tipos de equipamentos.

### Funcionalidades Principais

- ✅ **Backup Automatizado** de configurações de equipamentos de rede
- ✅ **Multi-Tenant** - Suporte a múltiplos clientes/provedores isolados
- ✅ **Agendamento** via Celery + Redis
- ✅ **Diff Visual** - Comparação de configurações entre versões
- ✅ **SSH Jump Host** - Suporte a conexões via bastion host
- ✅ **VPN Support** - L2TP, WireGuard, IPSec
- ✅ **Multi-Fabricante** - Mikrotik, Huawei, Cisco, Datacom, ZTE, etc.

### Equipamentos Suportados

| Fabricante | Tipos | Protocolo |
|------------|-------|-----------|
| Mikrotik | RouterOS, CCR, RB | SSH/API |
| Huawei | MA5600, MA5800, AR Series | Telnet/SSH |
| Cisco | Catalyst, ISR | SSH/Telnet |
| Datacom | DM4000, DM4610 | SSH/Telnet |
| ZTE | C300, C320, GPON | Telnet |

---

## 🏗️ ARQUITETURA E STACK TECNOLÓGICA

### Stack Backend

```
┌─────────────────────────────────────────┐
│         NGINX (Opcional)                │
│         Porta 80/443                    │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│      Flask Application                  │
│      Uvicorn ASGI Server                │
│      Porta 8000                         │
└──────────────┬──────────────────────────┘
               │
        ┌──────┴──────┐
        │             │
┌───────▼─────┐ ┌────▼──────┐
│ PostgreSQL  │ │  Redis    │
│ Porta 5433  │ │ Porta 6380│
└─────────────┘ └───────────┘
        │
┌───────▼─────────────┐
│  Celery Worker      │
│  (Background Tasks) │
└─────────────────────┘
```

### Tecnologias

| Componente | Tecnologia | Versão |
|------------|------------|--------|
| **Backend Framework** | Flask | 3.0.0 |
| **ASGI Server** | Uvicorn | 0.24.0 |
| **ORM** | SQLAlchemy | 2.0.23 |
| **Database** | PostgreSQL | 15-alpine |
| **Cache/Broker** | Redis | 7-alpine |
| **Task Queue** | Celery | 5.3.4 |
| **Frontend** | Jinja2 + TailwindCSS | - |
| **SSH Library** | Netmiko/Paramiko | 4.3.0 / 3.3.1 |
| **Containers** | Docker + Docker Compose | - |

### Dependências Python (requirements.txt)

```
fastapi==0.104.1
pydantic-settings==2.1.0
uvicorn[standard]==0.24.0
sqlalchemy==2.0.23
alembic==1.12.1
psycopg2-binary==2.9.9
redis==5.0.1
celery==5.3.4
python-multipart==0.0.6
python-jose[cryptography]==3.3.0
passlib==1.7.4
bcrypt==4.1.1
cryptography==41.0.7
jinja2==3.1.2
flask==3.0.0
flask-login==0.6.3
werkzeug==3.0.1
netmiko==4.3.0
paramiko==3.3.1
```

---

## 🔌 PORTAS UTILIZADAS

### Mapeamento de Portas (Host → Container)

| Serviço | Porta Host | Porta Container | Protocolo | Status |
|---------|------------|-----------------|-----------|--------|
| **Flask App (Web UI)** | `8000` | `8000` | HTTP | ⚠️ **CONFLITO COM PORTAINER TUNNEL** |
| **PostgreSQL** | `5433` | `5432` | TCP | ✅ Seguro |
| **Redis** | `6380` | `6379` | TCP | ✅ Seguro |

### ⚠️ ATENÇÃO: Conflito de Portas com Portainer

**Problema:** A porta `8000` do host está sendo usada pelo Backup Center. O Portainer Edge Agent também usa a porta `8000` por padrão.

**Soluções:**

1. **Opção 1 (Recomendada):** Alterar a porta do Backup Center no novo servidor:
   ```yaml
   # docker-compose.yml
   app:
     ports:
       - "8001:8000"  # Usar 8001 no host
   ```

2. **Opção 2:** Não expor a porta 8000 do Portainer (se não usar Edge Agent):
   ```bash
   docker run -d -p 9000:9000 -p 9443:9443 ... portainer/portainer-ce
   ```

3. **Opção 3:** Usar NGINX como proxy reverso:
   ```nginx
   # backup.exemplo.com → 8000
   # portainer.exemplo.com → 9000
   ```

---

## 🔑 CREDENCIAIS E ACESSOS

### Servidor SSH

```
Host: 168.194.13.17
Usuário: backupp
Senha: asdSD@91582685
Caminho do Projeto: /home/backupp/backup_project
```

### Aplicação Web

#### Acesso Tenant (Cliente/Provedor)
```
URL: http://168.194.13.17:8000/
Tenant: ajust-consulting
Email: audemario@ajustconsulting.com.br
Senha: 123456
```

#### Acesso Superadmin
```
URL: http://168.194.13.17:8000/superadmin
Email: admin@backupcenter.com
Senha: 123456
```

### Banco de Dados PostgreSQL

```
Host: localhost (dentro do Docker: db)
Porta Externa: 5433
Porta Interna: 5432
Database: backup_center
Usuário: backup_user
Senha: BackupSecure2024!
```

**Connection String:**
```
postgresql://backup_user:BackupSecure2024!@localhost:5433/backup_center
```

### Redis

```
Host: localhost (dentro do Docker: redis)
Porta Externa: 6380
Porta Interna: 6379
Senha: RedisSecure2024!
```

**Connection String:**
```
redis://:RedisSecure2024!@localhost:6380/0
```

### Chaves de Segurança (.env)

```bash
SECRET_KEY=your-secret-key-change-in-production-min-32-chars-12345678
ENCRYPTION_KEY=FbfA95Ns7rrcSJHNpXGLSWNH1jFB1FU6Bxlk-UiWEyI=
```

> ⚠️ **IMPORTANTE:** A `ENCRYPTION_KEY` é usada para criptografar senhas de equipamentos. Se mudar, os dados criptografados existentes não poderão ser descriptografados!

---

## 🐳 CONFIGURAÇÃO DOCKER

### Containers em Execução

| Container Name | Imagem | Função |
|----------------|--------|--------|
| `backup_center_app` | Custom (Python 3.11) | Aplicação Flask |
| `backup_center_db` | postgres:15-alpine | Banco de Dados |
| `backup_center_redis` | redis:7-alpine | Cache e Message Broker |
| `backup_center_celery` | Custom (Python 3.11) | Worker para tarefas assíncronas |

### docker-compose.yml (Resumo)

```yaml
services:
  db:
    image: postgres:15-alpine
    container_name: backup_center_db
    environment:
      POSTGRES_DB: backup_center
      POSTGRES_USER: backup_user
      POSTGRES_PASSWORD: ${DB_PASSWORD:-BackupSecure2024!}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5433:5432"
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    container_name: backup_center_redis
    command: redis-server --appendonly yes --requirepass ${REDIS_PASSWORD:-RedisSecure2024!}
    volumes:
      - redis_data:/data
    ports:
      - "6380:6379"
    restart: unless-stopped

  app:
    build: .
    container_name: backup_center_app
    environment:
      - DATABASE_URL=postgresql://backup_user:${DB_PASSWORD}@db:5432/backup_center
      - REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
      - SECRET_KEY=${SECRET_KEY}
      - ENCRYPTION_KEY=${ENCRYPTION_KEY}
    volumes:
      - ./storage:/app/storage
      - ./app:/app/app
    ports:
      - "8000:8000"
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

  celery:
    build: .
    container_name: backup_center_celery
    command: celery -A app.celery worker -l info
    environment:
      - DATABASE_URL=postgresql://backup_user:${DB_PASSWORD}@db:5432/backup_center
      - REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
      - SECRET_KEY=${SECRET_KEY}
      - ENCRYPTION_KEY=${ENCRYPTION_KEY}
    volumes:
      - ./storage:/app/storage
      - ./app:/app/app
    depends_on:
      - db
      - redis
    restart: unless-stopped

volumes:
  postgres_data:
  redis_data:
```

### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create storage directory
RUN mkdir -p /app/storage/backups

# Expose port
EXPOSE 8000

# Run application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 💾 BANCO DE DADOS

### Estrutura de Tabelas Principais

```sql
-- Tenants (Clientes/Provedores)
tenants
├── id (PK)
├── slug (unique)
├── name
├── domain
└── is_active

-- Usuários
users
├── id (PK)
├── email (unique)
├── password_hash
├── tenant_id (FK)
└── role

-- Grupos de Dispositivos
device_groups
├── id (PK)
├── name
├── tenant_id (FK)
├── connection_type (direct/vpn/jump_host)
├── uses_vpn
├── vpn_type
├── vpn_server
├── uses_jump_host
├── jump_host
├── jump_port
└── jump_username

-- Dispositivos
devices
├── id (PK)
├── name
├── ip_address
├── device_type_id (FK)
├── group_id (FK)
├── tenant_id (FK)
├── username
├── password_encrypted
└── ssh_key_encrypted

-- Backups
backups
├── id (PK)
├── device_id (FK)
├── tenant_id (FK)
├── status (pending/success/failed)
├── started_at
├── completed_at
├── file_path
└── file_size_bytes
```

### Migrações (Alembic)

O projeto usa **Alembic** para versionamento do banco. Arquivos em:
```
/home/backupp/backup_project/alembic/
└── versions/
    └── [migrations]
```

### Backup do Banco de Dados

**Exportar:**
```bash
docker exec backup_center_db pg_dump -U backup_user backup_center > backup_db_$(date +%Y%m%d).sql
```

**Importar no novo servidor:**
```bash
cat backup_db_20260130.sql | docker exec -i backup_center_db psql -U backup_user -d backup_center
```

---

## 📁 ESTRUTURA DE ARQUIVOS

### Árvore do Projeto

```
/home/backupp/backup_project/
│
├── app/                          # Código da aplicação
│   ├── __init__.py
│   ├── core/                     # Núcleo do sistema
│   │   ├── config.py            # Configurações gerais
│   │   ├── database.py          # Setup SQLAlchemy
│   │   ├── security.py          # Autenticação, criptografia
│   │   └── celery_app.py        # Configuração Celery
│   │
│   ├── models/                   # Modelos SQLAlchemy
│   │   ├── tenant.py
│   │   ├── user.py
│   │   ├── device.py
│   │   ├── device_group.py
│   │   ├── backup.py
│   │   └── ...
│   │
│   ├── services/                 # Lógica de negócio
│   │   ├── device_service.py
│   │   ├── backup_service.py
│   │   ├── backup_executor.py   # Execução de backups
│   │   └── scheduler_service.py
│   │
│   ├── web/                      # Rotas e Controllers
│   │   ├── auth/                # Login, registro
│   │   ├── tenant/              # Área do tenant
│   │   ├── superadmin/          # Área administrativa
│   │   └── api/                 # API REST
│   │
│   ├── templates/                # Templates Jinja2
│   │   ├── base.html
│   │   ├── auth/
│   │   ├── tenant/
│   │   │   ├── dashboard.html
│   │   │   ├── devices/
│   │   │   ├── groups/
│   │   │   └── backups/
│   │   ├── public/
│   │   │   └── landing.html     # Landing page premium
│   │   └── partials/
│   │
│   ├── static/                   # Arquivos estáticos
│   │   ├── css/
│   │   │   └── output.css       # TailwindCSS compilado
│   │   ├── js/
│   │   └── images/
│   │
│   └── scripts/                  # Scripts de backup
│       ├── mikrotik_backup.py
│       ├── huawei_backup.py
│       ├── cisco_backup.py
│       └── ...
│
├── storage/                      # Armazenamento de backups
│   └── backups/                 # ~451 MB de arquivos
│       ├── [tenant_slug]/
│       │   └── [device_name]/
│       │       └── [timestamp].rsc
│       └── ...
│
├── migrations/                   # SQL migrations manuais
│   └── add_jump_host.sql
│
├── alembic/                      # Migrações Alembic
│   ├── versions/
│   └── env.py
│
├── docker-compose.yml            # Configuração Docker
├── Dockerfile                    # Build da aplicação
├── requirements.txt              # Dependências Python
├── .env                          # Variáveis de ambiente (SENSÍVEL)
├── .env.example                  # Template de .env
├── main.py                       # Entrypoint da aplicação
├── alembic.ini                   # Config Alembic
└── README.md                     # Documentação básica
```

### Arquivos Críticos para Migração

| Arquivo | Importância | Observação |
|---------|-------------|------------|
| `storage/` | 🔴 CRÍTICO | **451 MB** de backups históricos |
| `postgres_data/` (volume) | 🔴 CRÍTICO | Dados do banco (dentro do Docker) |
| `redis_data/` (volume) | 🟡 MÉDIO | Cache, pode ser recriado |
| `.env` | 🔴 CRÍTICO | **NÃO PODE MUDAR** `ENCRYPTION_KEY` |
| `docker-compose.yml` | 🔴 CRÍTICO | Configuração dos serviços |
| `app/` | 🔴 CRÍTICO | Todo o código da aplicação |

---

## ⚙️ VARIÁVEIS DE AMBIENTE

### Arquivo .env (Completo)

```bash
# Database
DB_PASSWORD=BackupSecure2024!
DATABASE_URL=postgresql://backup_user:BackupSecure2024!@db:5432/backup_center

# Redis
REDIS_PASSWORD=RedisSecure2024!
REDIS_URL=redis://:RedisSecure2024!@redis:6379/0

# Security (NÃO ALTERAR ENCRYPTION_KEY!)
SECRET_KEY=your-secret-key-change-in-production-min-32-chars-12345678
ENCRYPTION_KEY=FbfA95Ns7rrcSJHNpXGLSWNH1jFB1FU6Bxlk-UiWEyI=

# Docker Compose
COMPOSE_PROJECT_NAME=backup_center
```

### ⚠️ ATENÇÃO ESPECIAL

**ENCRYPTION_KEY:**
- Esta chave criptografa senhas de equipamentos no banco
- Se alterar, **TODOS** os dados criptografados ficarão inacessíveis
- **NUNCA** regenere esta chave após ter dados em produção
- Na migração, **COPIE EXATAMENTE** o mesmo valor

---

## 🚀 PROCESSO DE MIGRAÇÃO

### Pré-requisitos no Novo Servidor

```bash
# 1. Docker e Docker Compose
sudo apt update
sudo apt install -y docker.io docker-compose git

# 2. Criar usuário (opcional, mas recomendado)
sudo useradd -m -s /bin/bash backupp
sudo passwd backupp  # Definir senha
sudo usermod -aG docker backupp

# 3. Liberar portas no firewall
sudo ufw allow 8000/tcp   # Ou 8001 se mudar
sudo ufw allow 5433/tcp   # PostgreSQL (se acesso externo)
sudo ufw allow 6380/tcp   # Redis (se acesso externo)
```

### Opção 1: Migração com Rsync (Recomendada)

**No servidor NOVO:**

```bash
# 1. Criar diretório
mkdir -p /home/backupp/backup_project

# 2. Rsync do servidor antigo
rsync -avz --progress \
  backupp@168.194.13.17:/home/backupp/backup_project/ \
  /home/backupp/backup_project/

# 3. Exportar volumes Docker do servidor antigo
# (fazer no servidor antigo primeiro)
```

**No servidor ANTIGO:**

```bash
cd /home/backupp/backup_project

# Backup do banco
docker exec backup_center_db pg_dump -U backup_user backup_center > db_backup.sql

# Parar containers
docker-compose down

# Transferir para o novo servidor
scp db_backup.sql novo_usuario@novo_servidor:/home/backupp/backup_project/
```

**No servidor NOVO (continuação):**

```bash
cd /home/backupp/backup_project

# Build e subir containers
docker-compose up -d --build

# Aguardar containers ficarem healthy
docker-compose ps

# Restaurar banco de dados
cat db_backup.sql | docker exec -i backup_center_db psql -U backup_user -d backup_center

# Restart para garantir
docker-compose restart

# Verificar logs
docker-compose logs -f app
```

### Opção 2: Migração com Git + Backup Manual

```bash
# No novo servidor
cd /home/backupp
git clone https://github.com/seu-repo/backup_project.git  # Se tiver Git
# OU fazer rsync apenas do código:
rsync -avz --exclude 'storage' --exclude '__pycache__' \
  backupp@168.194.13.17:/home/backupp/backup_project/ \
  /home/backupp/backup_project/

# Copiar .env
scp backupp@168.194.13.17:/home/backupp/backup_project/.env .env

# Copiar storage
rsync -avz --progress \
  backupp@168.194.13.17:/home/backupp/backup_project/storage/ \
  /home/backupp/backup_project/storage/

# Subir containers e restaurar banco (igual opção 1)
```

### Checklist de Migração

- [ ] Transferir código da aplicação (`app/`)
- [ ] Transferir `storage/` (451 MB de backups)
- [ ] Copiar `.env` **SEM ALTERAR** `ENCRYPTION_KEY`
- [ ] Copiar `docker-compose.yml` e `Dockerfile`
- [ ] Copiar `requirements.txt`
- [ ] Exportar dump do PostgreSQL
- [ ] Transferir `alembic/` (migrações)
- [ ] Build das imagens Docker
- [ ] Subir containers
- [ ] Restaurar banco de dados
- [ ] Testar login na aplicação
- [ ] Verificar se backups existentes aparecem
- [ ] Testar execução manual de um backup

---

## 🛠️ COMANDOS ÚTEIS

### Docker Compose

```bash
# Subir todos os serviços
docker-compose up -d

# Parar todos os serviços
docker-compose down

# Ver logs em tempo real
docker-compose logs -f

# Ver logs de um serviço específico
docker-compose logs -f app

# Rebuild e restart
docker-compose up -d --build

# Verificar status
docker-compose ps
```

### Docker Individual

```bash
# Acessar shell do container da aplicação
docker exec -it backup_center_app /bin/bash

# Acessar PostgreSQL
docker exec -it backup_center_db psql -U backup_user -d backup_center

# Acessar Redis CLI
docker exec -it backup_center_redis redis-cli -a RedisSecure2024!

# Ver logs do Celery
docker logs -f backup_center_celery
```

### Banco de Dados

```bash
# Backup manual
docker exec backup_center_db pg_dump -U backup_user backup_center > backup_$(date +%Y%m%d_%H%M%S).sql

# Restaurar backup
cat backup_20260130_143000.sql | docker exec -i backup_center_db psql -U backup_user -d backup_center

# Conectar ao banco
docker exec -it backup_center_db psql -U backup_user -d backup_center

# Listar tabelas
docker exec -it backup_center_db psql -U backup_user -d backup_center -c "\dt"
```

### Aplicação

```bash
# Criar superadmin (dentro do container)
docker exec -it backup_center_app python create_superadmin.py

# Rodar migrações Alembic
docker exec -it backup_center_app alembic upgrade head

# Ver rotas Flask
docker exec -it backup_center_app python -c "from app import create_app; app = create_app(); print(app.url_map)"
```

---

## 🔧 TROUBLESHOOTING

### Problema: Porta 8000 já em uso

**Sintoma:**
```
Error starting userland proxy: listen tcp4 0.0.0.0:8000: bind: address already in use
```

**Solução:**
```bash
# Ver o que está usando a porta
sudo lsof -i :8000
# OU
sudo netstat -tulpn | grep :8000

# Matar processo
sudo kill -9 [PID]

# Ou alterar porta no docker-compose.yml
# ports:
#   - "8001:8000"
```

### Problema: Container não sobe (unhealthy)

**Sintoma:**
```
backup_center_app exited with code 1
```

**Diagnóstico:**
```bash
# Ver logs completos
docker-compose logs app

# Ver eventos do Docker
docker events

# Tentar rodar comando manualmente
docker-compose run --rm app /bin/bash
```

**Soluções Comuns:**
- Verificar se `.env` existe
- Verificar se `DATABASE_URL` está correto
- Verificar se PostgreSQL já iniciou (healthcheck)
- Verificar permissões do diretório `storage/`

### Problema: Erro de conexão com banco

**Sintoma:**
```
sqlalchemy.exc.OperationalError: could not connect to server
```

**Solução:**
```bash
# Verificar se container do DB está rodando
docker ps | grep backup_center_db

# Verificar saúde do PostgreSQL
docker exec backup_center_db pg_isready -U backup_user

# Verificar variáveis de ambiente
docker exec backup_center_app env | grep DATABASE_URL

# Tentar conexão manual
docker exec -it backup_center_db psql -U backup_user -d backup_center
```

### Problema: Senhas de equipamentos não funcionam após migração

**Causa:** `ENCRYPTION_KEY` foi alterado

**Solução:**
1. **NÃO EXISTE** - Dados perdidos se chave mudou
2. Restaurar `.env` original com a chave correta
3. Restart da aplicação

**Prevenção:**
- Sempre fazer backup do `.env` original
- **NUNCA** regenerar `ENCRYPTION_KEY` em produção

### Problema: Storage não tem permissão

**Sintoma:**
```
PermissionError: [Errno 13] Permission denied: '/app/storage/backups/...'
```

**Solução:**
```bash
# No host
sudo chown -R 1000:1000 /home/backupp/backup_project/storage

# Ou dentro do container
docker exec backup_center_app chown -R www-data:www-data /app/storage
```

---

## 📞 CONTATOS E SUPORTE

### Informações do Tenant Principal
- **Cliente:** Ajust Consulting
- **Slug:** `ajust-consulting`
- **Contato:** audemario@ajustconsulting.com.br

### Arquivos com Contexto Adicional

1. `/home/backupp/backup_project/docs/HANDOVER_FOR_AI.md`
   - Contexto completo da reescrita do sistema
   - Decisões de arquitetura
   - Problemas corrigidos

2. `/home/backupp/backup_project/RELATORIO_ANALISE_SISTEMA.md`
   - Análise técnica do sistema legado
   - Comparação com sistema novo

3. `/home/backupp/backup_project/README.md`
   - Comandos básicos
   - Links úteis

---

## ✅ CHECKLIST FINAL PÓS-MIGRAÇÃO

- [ ] Aplicação acessível em `http://[novo_ip]:8000`
- [ ] Login de superadmin funciona
- [ ] Login de tenant funciona
- [ ] Dashboard carrega sem erros
- [ ] Dispositivos existentes aparecem na lista
- [ ] Backups históricos aparecem com datas corretas
- [ ] Diff de configuração funciona
- [ ] Executar um backup manual de teste funciona
- [ ] Celery worker está processando tarefas
- [ ] Redis está respondendo
- [ ] PostgreSQL aceita conexões
- [ ] Logs não mostram erros críticos
- [ ] Storage tem permissões corretas
- [ ] `.env` tem `ENCRYPTION_KEY` correta (dados descriptografam)

---

## 📝 NOTAS IMPORTANTES

1. **Portainer vs Backup Center:**
   - Se instalar Portainer, NÃO usar a porta 8000 no tunnel
   - Considere usar porta 8001 para o Backup Center no novo servidor

2. **Volumes Docker:**
   - Os volumes `postgres_data` e `redis_data` são criados automaticamente
   - Para migração completa, fazer dump/restore do PostgreSQL

3. **Rede Docker:**
   - Containers se comunicam pela rede `backup_center_default`
   - Não precisa expor todas as portas externamente

4. **Segurança:**
   - Alterar senhas padrão em produção
   - Considerar usar HTTPS (NGINX/Caddy)
   - Firewall bloqueando portas desnecessárias

5. **Performance:**
   - Celery executa backups em background
   - Redis armazena fila de tarefas
   - PostgreSQL é o único stateful service

---

**Fim do Guia de Migração**

> Este documento foi criado em 30/01/2026 como um snapshot completo do sistema Backup Center v2.0 para facilitar migração e handover para outras IAs ou desenvolvedores.
