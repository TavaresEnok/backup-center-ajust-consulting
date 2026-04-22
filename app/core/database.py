from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from app.core.config import settings

# engine = create_engine(settings.DATABASE_URL)
# For development/quick start with local postgres, ensure the URL is correct
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_timeout=30,
    pool_use_lifo=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def is_sqlite_engine() -> bool:
    """True quando DATABASE_URL é SQLite (ex.: testes/CI). Patches SQL específicos de PostgreSQL devem ser ignorados."""
    return engine.dialect.name == "sqlite"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
