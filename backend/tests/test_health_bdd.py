from fastapi.testclient import TestClient
from httpx import Response
from pytest_bdd import scenarios, then, when

scenarios("features/health.feature")


@when("the operator requests the health endpoint", target_fixture="health_response")
def request_health(client: TestClient) -> Response:
    response: Response = client.get("/health")
    return response


@then("the service reports it is ok")
def service_is_ok(health_response: Response) -> None:
    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok"}
