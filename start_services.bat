@echo off
REM Script para iniciar todos os serviços do Backup Center no Windows
REM Execute este script na raiz do projeto

echo ========================================
echo    BACKUP CENTER - Iniciando Servicos
echo ========================================

REM Verifica se Docker está rodando
docker info > nul 2>&1
if %errorlevel% neq 0 (
    echo [ERRO] Docker nao esta rodando. Inicie o Docker Desktop primeiro.
    pause
    exit /b 1
)

echo.
echo [1/4] Subindo containers Docker (PostgreSQL + Redis)...
docker-compose up -d
if %errorlevel% neq 0 (
    echo [ERRO] Falha ao subir containers Docker
    pause
    exit /b 1
)

echo.
echo [2/4] Aguardando servicos ficarem prontos...
timeout /t 5 /nobreak > nul

echo.
echo [3/4] Iniciando Celery Worker (nova janela)...
start "Celery Worker" cmd /k "cd /d %~dp0 && .venv\Scripts\activate && celery -A app.celery_app worker --loglevel=info --pool=solo"

echo.
echo [4/4] Iniciando Celery Beat (nova janela)...
start "Celery Beat" cmd /k "cd /d %~dp0 && .venv\Scripts\activate && celery -A app.celery_app beat --loglevel=info"

echo.
echo ========================================
echo    Servicos iniciados com sucesso!
echo ========================================
echo.
echo Containers Docker:
docker ps --format "  - {{.Names}}: {{.Status}}"
echo.
echo Proximos passos:
echo   1. Inicie o servidor: python start_server.py
echo   2. Acesse: http://localhost:8000/auth/login
echo.
echo Para parar tudo:
echo   - Feche as janelas do Celery
echo   - Execute: docker-compose down
echo.
pause
