from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from chess_tutor.models import DEFAULT_DISPLAY_NAME, Player
from chess_tutor.settings import Settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def database_url(settings: Settings) -> str:
    return f"sqlite:///{settings.database_path}"


def create_database_engine(settings: Settings) -> Engine:
    return create_engine(database_url(settings))


def run_migrations(settings: Settings) -> None:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    config = Config(BACKEND_ROOT / "alembic.ini")
    # configparser treats % as interpolation, so a path containing one must be escaped.
    config.set_main_option("sqlalchemy.url", database_url(settings).replace("%", "%%"))
    command.upgrade(config, "head")


def seed_default_player(engine: Engine) -> None:
    # shortcut: single-user app, so check-then-insert is not race-safe, revisit if multi-user.
    with Session(engine) as session:
        if session.scalar(select(Player.id).limit(1)) is None:
            session.add(Player(display_name=DEFAULT_DISPLAY_NAME))
            session.commit()
