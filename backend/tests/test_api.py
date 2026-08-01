"""Tests for the backend API."""

import pytest


@pytest.mark.anyio
async def test_health_check(client):
    """Test health check endpoint."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["db"] == "ok"
    assert data["version"] == "0.1.1"


@pytest.mark.anyio
async def test_root(client):
    """Test root endpoint."""
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "LLM Observatory"
    assert data["version"] == "0.1.1"


@pytest.mark.anyio
async def test_create_trace(client):
    """Test creating a trace."""
    response = await client.post("/v1/traces/", json={
        "name": "test-trace",
        "start_time": 1700000000.0,
        "status": "ok",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "test-trace"
    assert data["status"] == "ok"
    assert "id" in data


@pytest.mark.anyio
async def test_create_trace_with_spans(client):
    """Test creating a trace with spans."""
    response = await client.post("/v1/traces/", json={
        "name": "test-trace",
        "start_time": 1700000000.0,
        "status": "ok",
        "spans": [
            {
                "name": "llm-call",
                "span_type": "llm",
                "start_time": 1700000000.0,
                "end_time": 1700000001.0,
                "status": "ok",
                "tokens_input": 100,
                "tokens_output": 50,
                "cost_usd": 0.001,
            }
        ],
    })
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "test-trace"


@pytest.mark.anyio
async def test_list_traces(client):
    """Test listing traces."""
    # Create a trace first
    await client.post("/v1/traces/", json={
        "name": "test-trace",
        "start_time": 1700000000.0,
        "status": "ok",
    })

    response = await client.get("/v1/traces/")
    assert response.status_code == 200
    data = response.json()
    assert len(data["traces"]) >= 1
    assert data["traces"][0]["name"] == "test-trace"
    assert data["total"] >= 1


@pytest.mark.anyio
async def test_list_traces_with_status_filter(client):
    """Test listing traces with status filter."""
    # Create traces with different statuses
    await client.post("/v1/traces/", json={
        "name": "ok-trace",
        "start_time": 1700000000.0,
        "status": "ok",
    })
    await client.post("/v1/traces/", json={
        "name": "error-trace",
        "start_time": 1700000001.0,
        "status": "error",
    })

    response = await client.get("/v1/traces/?status=ok")
    assert response.status_code == 200
    data = response.json()
    assert all(t["status"] == "ok" for t in data["traces"])


@pytest.mark.anyio
async def test_get_trace(client):
    """Test getting a single trace."""
    create_response = await client.post("/v1/traces/", json={
        "name": "test-trace",
        "start_time": 1700000000.0,
        "status": "ok",
    })
    trace_id = create_response.json()["id"]

    response = await client.get(f"/v1/traces/{trace_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "test-trace"


@pytest.mark.anyio
async def test_get_trace_not_found(client):
    """Test getting a non-existent trace."""
    response = await client.get("/v1/traces/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_trace_invalid_id(client):
    """Test getting a trace with invalid ID format."""
    response = await client.get("/v1/traces/not-a-uuid")
    assert response.status_code == 400


@pytest.mark.anyio
async def test_get_trace_spans(client):
    """Test getting spans for a trace."""
    create_response = await client.post("/v1/traces/", json={
        "name": "test-trace",
        "start_time": 1700000000.0,
        "status": "ok",
        "spans": [
            {
                "name": "span-1",
                "span_type": "llm",
                "start_time": 1700000000.0,
                "end_time": 1700000001.0,
                "status": "ok",
            }
        ],
    })
    trace_id = create_response.json()["id"]

    response = await client.get(f"/v1/traces/{trace_id}/spans")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "span-1"


@pytest.mark.anyio
async def test_batch_create_spans(client):
    """Test batch creating spans."""
    response = await client.post("/v1/traces/batch", json={
        "spans": [
            {
                "trace_id": "00000000-0000-0000-0000-000000000001",
                "name": "span-1",
                "span_type": "llm",
                "start_time": 1700000000.0,
                "end_time": 1700000001.0,
                "status": "ok",
            },
            {
                "trace_id": "00000000-0000-0000-0000-000000000001",
                "name": "span-2",
                "span_type": "llm",
                "start_time": 1700000001.0,
                "end_time": 1700000002.0,
                "status": "ok",
            },
        ],
    })
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1  # Both spans belong to same trace


@pytest.mark.anyio
async def test_create_evaluation(client):
    """Test creating an evaluation."""
    # Create a trace first
    create_response = await client.post("/v1/traces/", json={
        "name": "test-trace",
        "start_time": 1700000000.0,
        "status": "ok",
    })
    trace_id = create_response.json()["id"]

    response = await client.post("/v1/evaluations/", json={
        "trace_id": trace_id,
        "evaluator_type": "rule_based",
        "score": 0.95,
        "criteria": {"max_latency": 1.0},
    })
    assert response.status_code == 200
    data = response.json()
    assert float(data["score"]) == 0.95


@pytest.mark.anyio
async def test_list_evaluations(client):
    """Test listing evaluations."""
    # Create a trace and evaluation
    create_response = await client.post("/v1/traces/", json={
        "name": "test-trace",
        "start_time": 1700000000.0,
        "status": "ok",
    })
    trace_id = create_response.json()["id"]

    await client.post("/v1/evaluations/", json={
        "trace_id": trace_id,
        "evaluator_type": "rule_based",
        "score": 0.95,
    })

    response = await client.get("/v1/evaluations/")
    assert response.status_code == 200
    data = response.json()
    assert len(data["evaluations"]) >= 1
    assert data["total"] >= 1


@pytest.mark.anyio
async def test_run_rule_based_evaluation(client):
    """Test running a rule-based evaluation."""
    # Create a trace with spans
    create_response = await client.post("/v1/traces/", json={
        "name": "test-trace",
        "start_time": 1700000000.0,
        "status": "ok",
        "spans": [
            {
                "name": "span-1",
                "span_type": "llm",
                "start_time": 1700000000.0,
                "end_time": 1700000001.0,
                "status": "ok",
                "cost_usd": 0.001,
            }
        ],
    })
    trace_id = create_response.json()["id"]

    response = await client.post("/v1/evaluations/run", json={
        "trace_id": trace_id,
        "evaluator_type": "rule_based",
        "criteria": [
            {"name": "max_latency", "description": "Max latency", "threshold": 5000},
            {"name": "max_cost", "description": "Max cost", "threshold": 0.1},
        ],
    })
    assert response.status_code == 200
    data = response.json()
    assert "score" in data
    assert "result" in data
    assert "passed" in data["result"]


@pytest.mark.anyio
async def test_analytics_summary(client):
    """Test analytics summary endpoint."""
    # Create some traces
    for i in range(3):
        await client.post("/v1/traces/", json={
            "name": f"trace-{i}",
            "start_time": 1700000000.0 + i,
            "status": "ok" if i < 2 else "error",
        })

    response = await client.get("/v1/analytics/summary?hours=24")
    assert response.status_code == 200
    data = response.json()
    assert data["total_traces"] == 3
    assert data["error_count"] == 1


@pytest.mark.anyio
async def test_analytics_cost_by_model(client):
    """Test cost-by-model endpoint."""
    await client.post("/v1/traces/", json={
        "name": "trace-with-cost",
        "start_time": 1700000000.0,
        "status": "ok",
        "spans": [
            {
                "name": "openai-call",
                "span_type": "llm",
                "start_time": 1700000000.0,
                "end_time": 1700000001.0,
                "status": "ok",
                "cost_usd": 0.005,
                "attributes": {"model": "gpt-4o"},
            }
        ],
    })

    response = await client.get("/v1/analytics/cost-by-model?hours=24")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert data[0]["model"] == "gpt-4o"
    assert float(data[0]["cost_usd"]) > 0


@pytest.mark.anyio
async def test_analytics_timeline(client):
    """Test timeline endpoint."""
    for i in range(3):
        await client.post("/v1/traces/", json={
            "name": f"trace-{i}",
            "start_time": 1700000000.0 + i,
            "status": "ok",
        })

    response = await client.get("/v1/analytics/timeline?hours=24&interval_minutes=60")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.anyio
async def test_delete_trace(client):
    """Test deleting a trace."""
    create_response = await client.post("/v1/traces/", json={
        "name": "trace-to-delete",
        "start_time": 1700000000.0,
        "status": "ok",
    })
    trace_id = create_response.json()["id"]

    response = await client.delete(f"/v1/traces/{trace_id}")
    assert response.status_code == 200

    # Verify it's gone
    response = await client.get(f"/v1/traces/{trace_id}")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_delete_trace_not_found(client):
    """Test deleting a non-existent trace."""
    response = await client.delete("/v1/traces/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_delete_evaluation(client):
    """Test deleting an evaluation."""
    # Create trace + eval
    create_response = await client.post("/v1/traces/", json={
        "name": "trace-for-eval-delete",
        "start_time": 1700000000.0,
        "status": "ok",
    })
    trace_id = create_response.json()["id"]

    eval_response = await client.post("/v1/evaluations/", json={
        "trace_id": trace_id,
        "evaluator_type": "rule_based",
        "score": 0.8,
    })
    eval_id = eval_response.json()["id"]

    response = await client.delete(f"/v1/evaluations/{eval_id}")
    assert response.status_code == 200

    # Verify it's gone
    response = await client.get(f"/v1/evaluations/{eval_id}")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_sdk_batch_export_contract(client):
    """Integration test: verify SDK OTLPExporter batch format is accepted by backend."""
    # This simulates what the Python SDK's OTLPExporter sends to /v1/traces/batch
    trace_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    spans = [
        {
            "id": "span-001",
            "trace_id": trace_id,
            "parent_id": None,
            "name": "openai.chat.completions.create",
            "span_type": "llm",
            "start_time": 1700000000.0,
            "end_time": 1700000001.5,
            "status": "ok",
            "input": {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]},
            "output": {"content": "Hi there!", "role": "assistant"},
            "tokens_input": 10,
            "tokens_output": 5,
            "cost_usd": 0.0001,
            "metadata": {},
            "attributes": {"model": "gpt-4o", "gen_ai.system": "openai"},
        }
    ]

    response = await client.post("/v1/traces/batch", json={"spans": spans})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "openai.chat.completions.create"

    # Verify spans are queryable
    trace_id_resp = data[0]["id"]
    spans_response = await client.get(f"/v1/traces/{trace_id_resp}/spans")
    assert spans_response.status_code == 200
    spans_data = spans_response.json()
    assert len(spans_data) == 1
    assert spans_data[0]["tokens_input"] == 10
    assert spans_data[0]["tokens_output"] == 5
    assert float(spans_data[0]["cost_usd"]) == 0.0001


# ── Search & Pagination Tests ────────────────────────────────────────


@pytest.mark.anyio
async def test_list_traces_with_search(client):
    """Test listing traces with server-side name search."""
    await client.post("/v1/traces/", json={
        "name": "production-chat",
        "start_time": 1700000000.0,
        "status": "ok",
    })
    await client.post("/v1/traces/", json={
        "name": "dev-summarize",
        "start_time": 1700000001.0,
        "status": "ok",
    })

    response = await client.get("/v1/traces/?search=chat")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["traces"][0]["name"] == "production-chat"


@pytest.mark.anyio
async def test_list_traces_pagination(client):
    """Test pagination info in list traces response."""
    for i in range(5):
        await client.post("/v1/traces/", json={
            "name": f"trace-{i}",
            "start_time": 1700000000.0 + i,
            "status": "ok",
        })

    response = await client.get("/v1/traces/?limit=2&offset=0")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert data["limit"] == 2
    assert data["offset"] == 0
    assert len(data["traces"]) == 2
    assert data["traces"][0]["name"] == "trace-4"  # Descending order


@pytest.mark.anyio
async def test_list_evaluations_pagination(client):
    """Test pagination info in list evaluations response."""
    create_response = await client.post("/v1/traces/", json={
        "name": "test-trace",
        "start_time": 1700000000.0,
        "status": "ok",
    })
    trace_id = create_response.json()["id"]

    for _ in range(3):
        await client.post("/v1/evaluations/", json={
            "trace_id": trace_id,
            "evaluator_type": "rule_based",
            "score": 0.8,
        })

    response = await client.get("/v1/evaluations/?limit=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["limit"] == 2
    assert len(data["evaluations"]) == 2


# ── Authentication Tests ────────────────────────────────────────────


@pytest.mark.anyio
async def test_auth_disabled_allows_all(client):
    """Test that when API key is not set, all requests are allowed."""
    # Auth is disabled in test env (no OBSERVATORY_API_KEY set)
    response = await client.get("/v1/traces/")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_auth_enabled_blocks_without_key(client):
    """Test that when API key is set, requests without key are blocked."""
    import app.auth as auth_mod
    old_key = auth_mod.API_KEY
    auth_mod.API_KEY = "test-secret-key"
    try:
        response = await client.get("/v1/traces/")
        assert response.status_code == 401
    finally:
        auth_mod.API_KEY = old_key


@pytest.mark.anyio
async def test_auth_enabled_allows_valid_key(client):
    """Test that when API key is set, requests with valid key are allowed."""
    import app.auth as auth_mod
    old_key = auth_mod.API_KEY
    auth_mod.API_KEY = "test-secret-key"
    try:
        response = await client.get(
            "/v1/traces/",
            headers={"Authorization": "Bearer test-secret-key"},
        )
        assert response.status_code == 200
    finally:
        auth_mod.API_KEY = old_key


@pytest.mark.anyio
async def test_auth_enabled_rejects_wrong_key(client):
    """Test that when API key is set, requests with wrong key are rejected."""
    import app.auth as auth_mod
    old_key = auth_mod.API_KEY
    auth_mod.API_KEY = "test-secret-key"
    try:
        response = await client.get(
            "/v1/traces/",
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert response.status_code == 403
    finally:
        auth_mod.API_KEY = old_key


@pytest.mark.anyio
async def test_auth_health_endpoint_unaffected(client):
    """Test that /health endpoint is not affected by auth."""
    import app.auth as auth_mod
    old_key = auth_mod.API_KEY
    auth_mod.API_KEY = "test-secret-key"
    try:
        response = await client.get("/health")
        assert response.status_code == 200
    finally:
        auth_mod.API_KEY = old_key


@pytest.mark.anyio
async def test_auth_root_endpoint_unaffected(client):
    """Test that / root endpoint is not affected by auth."""
    import app.auth as auth_mod
    old_key = auth_mod.API_KEY
    auth_mod.API_KEY = "test-secret-key"
    try:
        response = await client.get("/")
        assert response.status_code == 200
    finally:
        auth_mod.API_KEY = old_key


# ── Import Tests ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_import_traces(client):
    """Test importing traces from JSON."""
    import_payload = {
        "traces": [
            {
                "name": "imported-trace",
                "status": "ok",
                "start_time": 1700000000.0,
                "metadata": {"source": "migration"},
                "spans": [
                    {
                        "name": "imported-span",
                        "span_type": "llm",
                        "start_time": 1700000000.0,
                        "end_time": 1700000001.0,
                        "status": "ok",
                        "tokens_input": 50,
                        "tokens_output": 25,
                        "cost_usd": 0.0005,
                    }
                ],
            }
        ]
    }

    response = await client.post("/v1/traces/import", json=import_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["imported"] == 1
    assert len(data["trace_ids"]) == 1

    # Verify imported trace is queryable
    trace_id = data["trace_ids"][0]
    get_response = await client.get(f"/v1/traces/{trace_id}")
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "imported-trace"

    # Verify spans are queryable
    spans_response = await client.get(f"/v1/traces/{trace_id}/spans")
    assert spans_response.status_code == 200
    spans = spans_response.json()
    assert len(spans) == 1
    assert spans[0]["name"] == "imported-span"
    assert spans[0]["tokens_input"] == 50


@pytest.mark.anyio
async def test_import_traces_empty(client):
    """Test importing with no traces."""
    response = await client.post("/v1/traces/import", json={"traces": []})
    assert response.status_code == 200
    data = response.json()
    assert data["imported"] == 0
    assert data["trace_ids"] == []


# ── Evaluation Score Filter Tests ────────────────────────────────────


@pytest.mark.anyio
async def test_list_evaluations_score_filter(client):
    """Test filtering evaluations by score range."""
    create_response = await client.post("/v1/traces/", json={
        "name": "test-trace",
        "start_time": 1700000000.0,
        "status": "ok",
    })
    trace_id = create_response.json()["id"]

    # Create evaluations with different scores
    for score in [0.3, 0.5, 0.7, 0.9]:
        await client.post("/v1/evaluations/", json={
            "trace_id": trace_id,
            "evaluator_type": "rule_based",
            "score": score,
        })

    # Filter: min_score=0.6
    response = await client.get("/v1/evaluations/?min_score=0.6")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert all(e["score"] >= 0.6 for e in data["evaluations"])

    # Filter: max_score=0.4
    response = await client.get("/v1/evaluations/?max_score=0.4")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["evaluations"][0]["score"] <= 0.4

    # Filter: min=0.4, max=0.8
    response = await client.get("/v1/evaluations/?min_score=0.4&max_score=0.8")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2


# ── Export Tests ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_export_traces(client):
    """Test exporting traces with spans."""
    # Create a trace with spans
    create_response = await client.post("/v1/traces/", json={
        "name": "export-test",
        "start_time": 1700000000.0,
        "end_time": 1700000001.0,
        "status": "ok",
        "spans": [
            {
                "name": "llm_call",
                "span_type": "llm",
                "start_time": 1700000000.0,
                "end_time": 1700000000.5,
                "status": "ok",
                "tokens_input": 100,
                "tokens_output": 50,
                "cost_usd": 0.001,
            },
        ],
    })
    assert create_response.status_code == 200

    response = await client.get("/v1/traces/export")
    assert response.status_code == 200
    data = response.json()
    assert "traces" in data
    assert data["total"] >= 1
    exported = [t for t in data["traces"] if t["name"] == "export-test"]
    assert len(exported) == 1
    assert len(exported[0]["spans"]) == 1
    assert exported[0]["spans"][0]["tokens_input"] == 100


@pytest.mark.anyio
async def test_export_traces_with_status_filter(client):
    """Test export with status filter."""
    await client.post("/v1/traces/", json={
        "name": "ok-trace",
        "start_time": 1700000000.0,
        "status": "ok",
    })
    await client.post("/v1/traces/", json={
        "name": "error-trace",
        "start_time": 1700000000.0,
        "status": "error",
    })

    response = await client.get("/v1/traces/export?status=error")
    assert response.status_code == 200
    data = response.json()
    assert all(t["status"] == "error" for t in data["traces"])
    assert data["total"] == 1


@pytest.mark.anyio
async def test_export_traces_empty(client):
    """Test export with no traces."""
    response = await client.get("/v1/traces/export")
    assert response.status_code == 200
    data = response.json()
    assert data["traces"] == []
    assert data["total"] == 0


@pytest.mark.anyio
async def test_export_import_roundtrip(client):
    """Test export→import roundtrip preserves data."""
    from datetime import datetime

    # Create original trace
    await client.post("/v1/traces/", json={
        "name": "roundtrip",
        "start_time": 1700000000.0,
        "end_time": 1700000002.0,
        "status": "ok",
        "spans": [
            {
                "name": "span-a",
                "span_type": "llm",
                "start_time": 1700000000.0,
                "end_time": 1700000001.0,
                "status": "ok",
                "tokens_input": 200,
                "tokens_output": 100,
            },
        ],
    })

    # Export
    export_resp = await client.get("/v1/traces/export")
    export_data = export_resp.json()
    assert export_data["total"] >= 1

    # Convert export format (ISO datetime strings) to import format (timestamps)
    import_traces = []
    for t in export_data["traces"]:
        start_ts = datetime.fromisoformat(
            t["start_time"].replace("Z", "+00:00")
        ).timestamp()
        end_ts = (
            datetime.fromisoformat(
                t["end_time"].replace("Z", "+00:00")
            ).timestamp()
            if t["end_time"]
            else None
        )
        import_spans = []
        for s in t["spans"]:
            s_start = datetime.fromisoformat(
                s["start_time"].replace("Z", "+00:00")
            ).timestamp()
            s_end = (
                datetime.fromisoformat(
                    s["end_time"].replace("Z", "+00:00")
                ).timestamp()
                if s["end_time"]
                else None
            )
            import_spans.append({
                "name": s["name"],
                "span_type": s["span_type"],
                "start_time": s_start,
                "end_time": s_end,
                "status": s["status"],
                "tokens_input": s.get("tokens_input"),
                "tokens_output": s.get("tokens_output"),
            })
        import_traces.append({
            "name": t["name"],
            "start_time": start_ts,
            "end_time": end_ts,
            "status": t["status"],
            "spans": import_spans,
        })

    # Import
    import_resp = await client.post("/v1/traces/import", json={"traces": import_traces})
    assert import_resp.status_code == 200
    import_data = import_resp.json()
    assert import_data["imported"] >= 1


# ── Evaluation endpoints ─────────────────────────────────────────────


@pytest.mark.anyio
async def test_trace_evaluations(client):
    """Test GET /v1/traces/{id}/evaluations."""
    # Create a trace
    create_resp = await client.post("/v1/traces/", json={
        "name": "eval-test",
        "start_time": 1700000000.0,
        "end_time": 1700000001.0,
        "status": "ok",
    })
    trace_id = create_resp.json()["id"]

    # Create evaluation for it
    await client.post("/v1/evaluations/", json={
        "trace_id": trace_id,
        "evaluator_type": "rule_based",
        "score": 0.85,
        "criteria": {"latency": "pass"},
    })

    # Get evaluations for trace
    resp = await client.get(f"/v1/traces/{trace_id}/evaluations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["trace_id"] == trace_id
    assert data[0]["score"] == 0.85


@pytest.mark.anyio
async def test_trace_evaluations_empty(client):
    """Test GET /v1/traces/{id}/evaluations with no evaluations."""
    create_resp = await client.post("/v1/traces/", json={
        "name": "no-eval",
        "start_time": 1700000000.0,
        "status": "ok",
    })
    trace_id = create_resp.json()["id"]
    resp = await client.get(f"/v1/traces/{trace_id}/evaluations")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_trace_evaluations_invalid_id(client):
    """Test GET /v1/traces/{id}/evaluations with invalid ID."""
    resp = await client.get("/v1/traces/not-a-uuid/evaluations")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_bulk_delete_traces(client):
    """Test POST /v1/traces/batch-delete."""
    ids = []
    for i in range(3):
        r = await client.post("/v1/traces/", json={
            "name": f"delete-me-{i}",
            "start_time": 1700000000.0,
            "status": "ok",
        })
        ids.append(r.json()["id"])

    resp = await client.post("/v1/traces/batch-delete", json={"trace_ids": ids})
    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted"] == 3
    assert data["requested"] == 3

    # Verify they're gone
    for tid in ids:
        r = await client.get(f"/v1/traces/{tid}")
        assert r.status_code == 404


@pytest.mark.anyio
async def test_bulk_delete_partial(client):
    """Test bulk delete with some invalid IDs."""
    r = await client.post("/v1/traces/", json={
        "name": "keep-me",
        "start_time": 1700000000.0,
        "status": "ok",
    })
    valid_id = r.json()["id"]

    resp = await client.post("/v1/traces/batch-delete", json={
        "trace_ids": [valid_id, "not-a-uuid", "00000000-0000-0000-0000-000000000000"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted"] == 1


@pytest.mark.anyio
async def test_evaluations_summary(client):
    """Test GET /v1/evaluations/summary."""
    # Create some evaluations
    await client.post("/v1/evaluations/", json={
        "trace_id": "00000000-0000-0000-0000-000000000001",
        "evaluator_type": "rule_based",
        "score": 0.9,
    })
    await client.post("/v1/evaluations/", json={
        "trace_id": "00000000-0000-0000-0000-000000000002",
        "evaluator_type": "llm_judge",
        "score": 0.7,
    })

    resp = await client.get("/v1/evaluations/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2
    assert "by_type" in data
    assert "pass_rate" in data


@pytest.mark.anyio
async def test_list_sessions(client):
    """Test GET /v1/analytics/sessions."""
    # Create traces with session IDs
    await client.post("/v1/traces/", json={
        "name": "session-trace",
        "session_id": "11111111-1111-1111-1111-111111111111",
        "start_time": 1700000000.0,
        "status": "ok",
    })

    resp = await client.get("/v1/analytics/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    if data:
        assert "session_id" in data[0]
        assert "trace_count" in data[0]
        assert "total_cost_usd" in data[0]
