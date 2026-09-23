from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from api.main import app
from data.db import get_conn


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health_reports_populated_caches(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["caches_loaded"] is True
    assert payload["career_fighters_cached"] > 1_000
    assert payload["search_index_size"] > 4_000


def test_search_is_case_insensitive(client: TestClient) -> None:
    lower = client.get("/api/fighters", params={"search": "makhachev"})
    upper = client.get("/api/fighters", params={"search": "MAKHACHEV"})
    assert lower.status_code == upper.status_code == 200
    assert lower.json()["results"] == upper.json()["results"]
    assert any("Makhachev" in row["name"] for row in lower.json()["results"])


def test_matchup_validates_ids(client: TestClient) -> None:
    same = client.get("/api/matchup", params={"a": 1, "b": 1})
    missing = client.get("/api/matchup", params={"a": -1, "b": -2})
    assert same.status_code == 422
    assert missing.status_code == 404


def test_matchup_prediction_is_order_invariant(client: TestClient) -> None:
    conn = get_conn()
    try:
        pair = conn.execute(
            """
            SELECT a.fighter_id AS a_id, b.fighter_id AS b_id
            FROM ratings_current a
            JOIN ratings_current b ON a.division = b.division
            WHERE a.fighter_id < b.fighter_id
              AND a.n_fights >= 5 AND b.n_fights >= 5
            LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()

    assert pair is not None
    forward = client.get(
        "/api/matchup", params={"a": pair["a_id"], "b": pair["b_id"]}
    )
    reverse = client.get(
        "/api/matchup", params={"a": pair["b_id"], "b": pair["a_id"]}
    )
    assert forward.status_code == reverse.status_code == 200
    p_forward = forward.json()["prediction"]["prob_red"]
    p_reverse = reverse.json()["prediction"]["prob_red"]
    assert p_forward + p_reverse == pytest.approx(1.0, abs=1e-6)


def test_refresh_endpoint_is_disabled_without_secret(client: TestClient) -> None:
    previous = os.environ.pop("UFC_ELO_REFRESH_TOKEN", None)
    try:
        response = client.post("/api/refresh")
    finally:
        if previous is not None:
            os.environ["UFC_ELO_REFRESH_TOKEN"] = previous
    assert response.status_code == 404


def test_refresh_endpoint_requires_configured_secret(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UFC_ELO_REFRESH_TOKEN", "test-refresh-token")
    denied = client.post("/api/refresh")
    allowed = client.post(
        "/api/refresh", headers={"X-Refresh-Token": "test-refresh-token"}
    )
    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "refreshed"
