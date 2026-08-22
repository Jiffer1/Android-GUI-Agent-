from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config.settings import settings
from pathlib import Path


Path("data").mkdir(exist_ok=True)

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Drop legacy task tables and create the conversation schema.

    Idempotent: safe to call on every startup. Legacy tables (tasks /
    task_steps) are not migrated — their data is intentionally discarded.
    """
    from sqlalchemy import text

    import app.storage.models  # noqa: F401  (register mappers before create_all)

    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS tasks"))
        conn.execute(text("DROP TABLE IF EXISTS task_steps"))
        conn.commit()
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
