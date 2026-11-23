from alembic import context
from sqlalchemy import create_engine

from chess_tutor.database import database_url
from chess_tutor.models import Base
from chess_tutor.settings import Settings

config = context.config
# Programmatic runs (app startup, tests) pass their own URL; the CLI falls back to Settings.
url = config.get_main_option("sqlalchemy.url") or database_url(Settings(_env_file=None))

if context.is_offline_mode():
    context.configure(
        url=url, target_metadata=Base.metadata, render_as_batch=True, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=Base.metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
