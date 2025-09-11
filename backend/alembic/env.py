import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# --- Make 'app' importable when running Alembic from backend/ ---
# backend/alembic/env.py  ->  add backend/ to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/..")

# --- Load environment variables (DATABASE_URL) ---
# This assumes your .env is at backend/.env. If you keep it in backend/app/.env,
# change the path below to ("..", "app", ".env")
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

# --- Import your SQLAlchemy Base metadata ---
from app.models import Base  # noqa: E402

# -----------------------------------------------------------------------------

# Alembic Config object (reads alembic.ini)
config = context.config

# If DATABASE_URL is set (from .env or environment), inject it into Alembic.
db_url = os.getenv("DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

# Configure logging from alembic.ini if present
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# This is what enables `alembic revision --autogenerate`
target_metadata = Base.metadata

# -----------------------------------------------------------------------------

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no DBAPI)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,          # detect column type changes
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (with an Engine)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,      # detect column type changes
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
