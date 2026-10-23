"""Size limits, rate-limit identity and deployment defaults (audit 2026-10-16)."""

from pathlib import Path

import pytest
import yaml

from app.limits import BodyLimitMiddleware, bounded_json

ROOT = Path(__file__).resolve().parents[2]


def _span(**kw):
    base = {"name": "s", "span_type": "llm", "start_time": 1_750_000_000.0, "status": "ok"}
    return {**base, **kw}


@pytest.mark.anyio
async def test_too_many_spans_and_oversized_json_are_rejected_before_storing(client):
    many = {"spans": [_span() for _ in range(1001)]}
    assert (await client.post("/v1/traces/batch", json=many)).status_code == 422
    big = {"spans": [_span(attributes={"blob": "x" * (300 * 1024)})]}
    assert (await client.post("/v1/traces/batch", json=big)).status_code == 422
    deep: dict = {}
    node = deep
    for _ in range(30):
        node["n"] = {}
        node = node["n"]
    assert (
        await client.post("/v1/traces/batch", json={"spans": [_span(input=deep)]})
    ).status_code == 422
    assert (
        await client.post("/v1/traces/batch", json={"spans": [_span(name="n" * 300)]})
    ).status_code == 422
    trace = {"name": "t", "start_time": 1_750_000_000.0, "spans": [_span() for _ in range(501)]}
    assert (await client.post("/v1/traces/", json=trace)).status_code == 422
    imports = {"traces": [{"name": "t"} for _ in range(201)]}
    assert (await client.post("/v1/traces/import", json=imports)).status_code == 422


@pytest.mark.anyio
async def test_reasonable_payloads_still_pass(client):
    ok = {"spans": [_span(input={"prompt": "p" * 50_000}, attributes={"k": "v"})]}
    assert (await client.post("/v1/traces/batch", json=ok)).status_code == 200


def test_bounded_json_limits_size_and_depth():
    assert bounded_json(None) is None and bounded_json({"a": 1}) == {"a": 1}
    with pytest.raises(ValueError, match="bytes"):
        bounded_json({"a": "x" * (300 * 1024)})


async def _call_app(app, body_chunks, headers):
    sent = []
    queue = [
        {"type": "http.request", "body": c, "more_body": i < len(body_chunks) - 1}
        for i, c in enumerate(body_chunks)
    ]

    async def receive():
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/", "headers": headers}
    await BodyLimitMiddleware(app, max_bytes=100)(scope, receive, send)
    return sent


@pytest.mark.anyio
async def test_body_limit_rejects_declared_and_streamed_oversize():
    reads = []

    async def app(scope, receive, send):
        while True:
            message = await receive()
            reads.append(message["type"])
            if message["type"] != "http.request" or not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    declared = await _call_app(app, [b"x"], [(b"content-length", b"500")])
    assert declared[0]["status"] == 413 and not reads  # never reached the application
    streamed = await _call_app(app, [b"x" * 60, b"x" * 60], [])  # no Content-Length
    assert streamed[0]["status"] == 413
    small = await _call_app(app, [b"x" * 10], [(b"content-length", b"10")])
    assert small[0]["status"] == 200


def test_rate_limit_identity_does_not_trust_unvalidated_tokens(monkeypatch):
    from types import SimpleNamespace

    import app.ratelimit as rl

    def request(token, ip="203.0.113.7"):
        return SimpleNamespace(
            headers={"Authorization": f"Bearer {token}"},
            client=SimpleNamespace(host=ip),
            scope={"client": (ip, 0)},
        )

    monkeypatch.setattr(rl, "API_KEY", "")
    keys = {rl._rate_limit_key(request(f"fake-{i}")) for i in range(5)}
    assert len(keys) == 1  # rotating fake tokens does not buy a fresh budget
    monkeypatch.setattr(rl, "API_KEY", "real-key")
    assert rl._rate_limit_key(request("real-key")) == "apikey:valid"
    assert rl._rate_limit_key(request("wrong")) == rl._rate_limit_key(request("other"))
    assert rl._rate_limit_key(request("wrong")) != "apikey:valid"


def test_one_compose_file_for_docker_and_podman():
    # podman-compose merges every default file it finds (compose.yaml, podman-compose.yml,
    # docker-compose.yml...), so two copies duplicated the healthcheck and broke `up`.
    defaults = ["compose.yaml", "compose.yml", "podman-compose.yaml", "podman-compose.yml",
                "docker-compose.yml", "docker-compose.yaml", "container-compose.yml"]
    assert [n for n in defaults if (ROOT / n).exists()] == ["compose.yaml"]


def test_compose_defaults_are_loopback_only_with_no_known_credentials():
    text = (ROOT / "compose.yaml").read_text()
    data = yaml.safe_load(text)
    services = data["services"]
    assert "ports" not in services["postgres"]  # the database is not published
    for service in ("backend", "dashboard"):
        for port in services[service]["ports"]:
            assert str(port).startswith("${OBSERVER_BIND:-127.0.0.1}:"), port
    assert "POSTGRES_PASSWORD:?" in services["postgres"]["environment"]["POSTGRES_PASSWORD"]
    assert "postgres:postgres@" not in text
    assert "OBSERVATORY_API_KEY" in services["backend"]["environment"]
