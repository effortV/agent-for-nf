from __future__ import annotations

from collections.abc import Generator
from importlib import import_module

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
if settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
elif settings.database_url.startswith("postgresql+psycopg"):
    # RQ uses forked workhorse processes. Disabling psycopg's automatic server-side
    # statement preparation prevents a child from reusing a prepared-statement name
    # that belongs to an inherited PostgreSQL connection.
    connect_args = {"prepare_threshold": None}
else:
    connect_args = {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    import_module("app.models")
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
