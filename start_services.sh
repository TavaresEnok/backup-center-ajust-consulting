#!/bin/bash
# Script para iniciar todos os serviços do Backup Center
# Execute: chmod +x start_services.sh && ./start_services.sh

echo "========================================"
echo "   BACKUP CENTER - Iniciando Servicos"
echo "========================================"

# Verifica se Docker está rodando
if ! docker info > /dev/null 2>&1; then
    echo "[ERRO] Docker não está rodando. Inicie o Docker primeiro."
    exit 1
fi

echo ""
echo "[1/4] Subindo containers Docker (PostgreSQL + Redis)..."
docker-compose up -d

echo ""
echo "[2/4] Aguardando serviços ficarem prontos..."
sleep 5

echo ""
echo "[3/4] Iniciando Celery Worker (background)..."
celery -A app.celery_app worker --loglevel=info &
WORKER_PID=$!

echo ""
echo "[4/4] Iniciando Celery Beat (background)..."
celery -A app.celery_app beat --loglevel=info &
BEAT_PID=$!

echo ""
echo "========================================"
echo "   Serviços iniciados com sucesso!"
echo "========================================"
echo ""
echo "PIDs:"
echo "  - Celery Worker: $WORKER_PID"
echo "  - Celery Beat: $BEAT_PID"
echo ""
echo "Containers Docker:"
docker ps --format "  - {{.Names}}: {{.Status}}"
echo ""
echo "Próximos passos:"
echo "  1. Inicie o servidor: python start_server.py"
echo "  2. Acesse: http://localhost:8000/auth/login"
echo ""
echo "Para parar tudo:"
echo "  kill $WORKER_PID $BEAT_PID"
echo "  docker-compose down"
