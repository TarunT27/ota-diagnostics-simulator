from __future__ import annotations

from fastapi.testclient import TestClient

from ota_simulator.api import create_app


def test_health_and_security_headers(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"success": True, "data": {"status": "ok"}}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_summary_and_controller_collection_contract(client: TestClient) -> None:
    summary = client.get("/api/v1/summary")
    controllers = client.get("/api/v1/controllers")

    assert summary.status_code == 200
    assert summary.json()["data"]["total_controllers"] == 6
    assert controllers.status_code == 200
    assert len(controllers.json()["data"]) == 6


def test_update_endpoint_runs_successful_simulation(client: TestClient) -> None:
    response = client.post(
        "/api/v1/controllers/telematics/updates",
        json={"target_version": "2.1.0", "fault": "none"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["current_version"] == "2.1.0"
    events = client.get("/api/v1/events?controller_id=telematics&limit=20")
    assert [event["stage"] for event in events.json()["data"]][-1] == "completed"


def test_api_rejects_unknown_fields_and_bad_versions_without_mutating_state(
    client: TestClient,
) -> None:
    before = client.get("/api/v1/controllers/gateway").json()["data"]
    response = client.post(
        "/api/v1/controllers/gateway/updates",
        json={"target_version": "not-a-version", "fault": "none", "admin": True},
    )
    after = client.get("/api/v1/controllers/gateway").json()["data"]

    assert response.status_code == 422
    assert response.json()["success"] is False
    assert response.json()["error"]["code"] == "validation_error"
    assert before == after


def test_api_returns_safe_not_found_error(client: TestClient) -> None:
    response = client.get("/api/v1/controllers/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {
        "success": False,
        "error": {"code": "controller_not_found", "message": "Controller was not found."},
    }


def test_api_rejects_invalid_state_transition(client: TestClient) -> None:
    response = client.post("/api/v1/controllers/gateway/rollback")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "state_conflict"


def test_reset_endpoint_restores_baseline(client: TestClient) -> None:
    client.post(
        "/api/v1/controllers/gateway/updates",
        json={"target_version": "2.0.0", "fault": "none"},
    )

    response = client.post("/api/v1/reset")

    assert response.status_code == 200
    controller = client.get("/api/v1/controllers/gateway").json()["data"]
    assert controller["current_version"] == "1.3.2"


def test_dashboard_and_assets_are_served_locally(client: TestClient) -> None:
    page = client.get("/")
    stylesheet = client.get("/static/styles.css")
    script = client.get("/static/app.js")

    assert page.status_code == 200
    assert "OTA Fleet Diagnostics Lab" in page.text
    assert "Fleet command center" in page.text
    assert "https://" not in page.text
    assert stylesheet.status_code == 200
    assert script.status_code == 200


def test_openapi_publishes_typed_contract_without_broken_cdn_ui(client: TestClient) -> None:
    schema = client.get("/openapi.json")

    assert schema.status_code == 200
    response_schema = schema.json()["paths"]["/api/v1/controllers"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert "$ref" in response_schema
    update_validation_schema = schema.json()["paths"][
        "/api/v1/controllers/{controller_id}/updates"
    ]["post"]["responses"]["422"]["content"]["application/json"]["schema"]
    assert update_validation_schema["$ref"].endswith("ErrorEnvelope")
    assert client.get("/docs").status_code == 404


def test_mutation_rejects_cross_origin_browser_request(client: TestClient) -> None:
    response = client.post(
        "/api/v1/reset",
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"


def test_update_rejects_oversized_version_before_domain_parsing(client: TestClient) -> None:
    response = client.post(
        "/api/v1/controllers/gateway/updates",
        json={"target_version": f"1.{('0' * 4000)}.0", "fault": "none"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_chunked_mutation_body_cannot_bypass_size_limit(client: TestClient) -> None:
    body = b'{"target_version":"2.0.0","fault":"none","padding":"' + (b"x" * 20_000) + b'"}'
    response = client.post(
        "/api/v1/controllers/gateway/updates",
        content=iter((body,)),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_unexpected_errors_use_safe_json_envelope_and_security_headers() -> None:
    class FailingService:
        def summary(self) -> dict[str, int]:
            raise RuntimeError("sensitive persistence detail")

    app = create_app(service=FailingService())  # type: ignore[arg-type]
    with TestClient(app, raise_server_exceptions=False) as failing_client:
        response = failing_client.get("/api/v1/summary")

    assert response.status_code == 500
    assert response.json() == {
        "success": False,
        "error": {"code": "internal_error", "message": "An unexpected error occurred."},
    }
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sensitive" not in response.text
