from pathlib import Path
from sqlite3 import Connection as SQLiteConnection

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Engine, create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import ConnectionPoolEntry

from chess_tutor.models import DEFAULT_DISPLAY_NAME, Player
from chess_tutor.settings import Settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def database_url(settings: Settings) -> URL:
    # URL.create escapes characters such as ? and # that string interpolation would misparse.
    return URL.create("sqlite", database=str(settings.database_path))


def create_database_engine(settings: Settings) -> Engine:
    engine = create_engine(database_url(settings))
    event.listen(engine, "connect", enable_foreign_keys)
    return engine


def enable_foreign_keys(connection: SQLiteConnection, _: ConnectionPoolEntry) -> None:
    # SQLite ignores FOREIGN KEY constraints unless each connection opts in.
    connection.execute("PRAGMA foreign_keys = ON")


def run_migrations(settings: Settings) -> None:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    config = Config(BACKEND_ROOT / "alembic.ini")
    # configparser treats % as interpolation, so a path containing one must be escaped.
    url = database_url(settings).render_as_string(hide_password=False)
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")


def seed_default_player(engine: Engine) -> None:
    # shortcut: single-user app, so check-then-insert is not race-safe, revisit if multi-user.
    with Session(engine) as session:
        if session.scalar(select(Player.id).limit(1)) is None:
            session.add(Player(display_name=DEFAULT_DISPLAY_NAME))
            session.commit()
