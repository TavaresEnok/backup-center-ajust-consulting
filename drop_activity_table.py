from sqlalchemy import text
from app.core.database import SessionLocal

def drop_activity_logs():
    db = SessionLocal()
    try:
        print("Dropando tabela activity_logs para recriação...")
        db.execute(text("DROP TABLE IF EXISTS activity_logs CASCADE;"))
        db.commit()
        print("Tabela removida com sucesso!")
    except Exception as e:
        print(f"Erro: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    drop_activity_logs()
