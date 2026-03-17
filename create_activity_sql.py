from sqlalchemy import text
from app.core.database import SessionLocal

def create_table_sql():
    db = SessionLocal()
    sql = """
    CREATE TABLE IF NOT EXISTS activity_logs (
        id VARCHAR(36) PRIMARY KEY,
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc'),
        updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc'),
        tenant_id VARCHAR(36) REFERENCES tenants(id),
        user_id VARCHAR(36) REFERENCES users(id),
        action VARCHAR(50) NOT NULL,
        details TEXT,
        ip_address VARCHAR(45)
    );
    """
    try:
        print("Executando CREATE TABLE activity_logs...")
        db.execute(text(sql))
        db.commit()
        print("Sucesso!")
    except Exception as e:
        print(f"Erro: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    create_table_sql()
