"""/ws/live follows the same API-key rule as the REST routes (audit 2026-10-14: it had no check)."""

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.auth as auth_mod
from app.main import app


@pytest.fixture
def with_key():
    old = auth_mod.API_KEY
    auth_mod.API_KEY = "test-secret-key"
    yield
    auth_mod.API_KEY = old


def test_ws_live_is_open_without_a_configured_key():
    with TestClient(app) as client, client.websocket_connect("/ws/live") as ws:
        ws.send_text("ping")
        assert ws.receive_json()["type"] == "pong"


def test_ws_live_refuses_a_missing_or_wrong_key(with_key):
    with TestClient(app) as client:
        for url, headers in (
            ("/ws/live", {}),
            ("/ws/live?api_key=nope", {}),
            ("/ws/live", {"Authorization": "Bearer nope"}),
        ):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(url, headers=headers) as ws:
                    ws.receive_text()


def test_ws_live_accepts_the_key_in_header_or_query(with_key):
    with TestClient(app) as client:
        for url, headers in (
            ("/ws/live", {"Authorization": "Bearer test-secret-key"}),
            ("/ws/live?api_key=test-secret-key", {}),
        ):
            with client.websocket_connect(url, headers=headers) as ws:
                ws.send_text("ping")
                assert ws.receive_json()["type"] == "pong"
