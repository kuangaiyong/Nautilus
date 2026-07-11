"""
Database configuration and session management.
"""

from dotenv import load_dotenv

# Load environment variables BEFORE any configuration
load_dotenv()
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from typing import Generator
import os

from models.database import Base

# 私有化部署固定使用 MySQL：DATABASE_URL 必须显式配置（.env），不再提供 SQLite 兜底
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL 未设置。私有化部署使用 MySQL，例如 "
        "mysql+pymysql://user:pass@127.0.0.1:3306/nautilus_private?charset=utf8mb4"
    )

# MySQL configuration with optimized connection pool
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,  # Verify connections before using
    pool_size=int(os.getenv("DATABASE_POOL_SIZE", "20")),  # Increased from 10
    max_overflow=int(os.getenv("DATABASE_MAX_OVERFLOW", "40")),  # Increased from 20
    pool_recycle=int(os.getenv("DATABASE_POOL_RECYCLE", "3600")),  # Recycle after 1 hour
    pool_timeout=int(os.getenv("DATABASE_POOL_TIMEOUT", "30")),  # Wait 30s for connection
    echo=os.getenv("DEBUG", "false").lower() == "true"
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Initialize database tables."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    Get database session.

    Usage:
        from utils.database import get_db

        @app.get("/items")
        def read_items(db: Session = Depends(get_db)):
            return db.query(Item).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_context():
    """
    Get database session as context manager.

    Usage:
        from utils.database import get_db_context

        with get_db_context() as db:
            items = db.query(Item).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_engine():
    """Get database engine instance."""
    return engine
