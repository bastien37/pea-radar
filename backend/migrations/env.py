"""Environnement Alembic : l'URL et les modèles viennent de l'application."""

from alembic import context
from sqlalchemy import create_engine

from app import models  # noqa: F401 — enregistre les tables sur Base.metadata
from app.config import get_settings
from app.db import Base

target_metadata = Base.metadata


def run_migrations() -> None:
    url = context.config.get_main_option("sqlalchemy.url") or get_settings().database_url
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                          render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(url)
    with engine.connect() as connection:
        # render_as_batch : SQLite ne sait pas modifier une table en place.
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations()
