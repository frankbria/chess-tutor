from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Chess Tutor")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
import os
