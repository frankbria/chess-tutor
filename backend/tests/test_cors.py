from fastapi.testclient import TestClient

from chess_tutor.main import create_app
from chess_tutor.settings import Settings

ALLOWED_ORIGIN = "http://localhost:4000"


def preflight(client: TestClient, origin: str) -> dict[str, str]:
    response = client.options(
        "/health", headers={"Origin": origin, "Access-Control-Request-Method": "GET"}
    )
    return dict(response.headers)


def test_configured_origin_is_allowed_and_others_are_not() -> None:
    settings = Settings(
        _env_file=None, llm_provider="none", frontend_origins=[ALLOWED_ORIGIN], maia_enabled=False
    )
    client = TestClient(create_app(settings))

    assert preflight(client, ALLOWED_ORIGIN)["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "access-control-allow-origin" not in preflight(client, "http://evil.example")


def test_browser_can_post_json_from_the_frontend_origin() -> None:
    # A JSON POST is not a "simple" request, so the browser preflights method and header.
    settings = Settings(
        _env_file=None, llm_provider="none", frontend_origins=[ALLOWED_ORIGIN], maia_enabled=False
    )
    client = TestClient(create_app(settings))

    response = client.options(
        "/games",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert "POST" in response.headers["access-control-allow-methods"]
