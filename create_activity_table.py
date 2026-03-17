"""
Script para criar a tabela de logs de atividade.
"""
from app.core.database import engine, Base
# Importar __init__ carrega todos os modelos e registra no Base.metadata
import app.models 

def create_tables():
    print("Criando tabela activity_logs...")
    # Cria apenas as tabelas que ainda não existem
    Base.metadata.create_all(bind=engine)
    print("Tabelas verificadas/criadas com sucesso!")

if __name__ == "__main__":
    create_tables()
