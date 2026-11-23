from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chess_tutor.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Chess Tutor")
    app.add_middleware(CORSMiddleware, allow_origins=settings.frontend_origins)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
