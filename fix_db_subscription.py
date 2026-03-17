from app.core.database import engine, Base
from sqlalchemy import text
from app.models.payment import Subscription

def fix_table():
    print("Dropping subscriptions table...")
    with engine.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT")
        try:
            conn.execute(text("DROP TABLE subscriptions CASCADE"))
            print("Dropped subscriptions")
        except Exception as e:
            print(f"Error dropping: {e}")

    print("Recreating subscriptions table...")
    # Recreate only the tables that are missing (which includes subscriptions now)
    Base.metadata.create_all(bind=engine)
    print("Fixed!")

if __name__ == "__main__":
    fix_table()
