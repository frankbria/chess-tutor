from alembic import context
from sqlalchemy import create_engine

from chess_tutor.database import database_url
from chess_tutor.models import Base
from chess_tutor.settings import Settings

config = context.config
# Programmatic runs (app startup, tests) pass their own URL; the CLI falls back to Settings.
configured_url = config.get_main_option("sqlalchemy.url")
if configured_url is None:
    # Migrations only need the database path, so the CLI must not require an LLM key.
    cli_settings = Settings(llm_provider="none")
    cli_settings.database_path.parent.mkdir(parents=True, exist_ok=True)
url = configured_url or database_url(cli_settings)

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
