from fastapi.testclient import TestClient

from main import app


def test_loopback_request_gets_security_headers():
    client = TestClient(
        app, base_url="http://localhost",
        client=("127.0.0.1", 43123),
    )

    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "object-src 'none'" in response.headers["content-security-policy"]


def test_non_loopback_client_is_rejected():
    client = TestClient(
        app, base_url="http://localhost",
        client=("203.0.113.20", 43123),
    )

    response = client.get("/health")

    assert response.status_code == 403


def test_unexpected_host_is_rejected():
    client = TestClient(
        app, base_url="http://localhost",
        client=("127.0.0.1", 43123),
    )

    response = client.get("/health", headers={"host": "attacker.example"})

    assert response.status_code == 403
