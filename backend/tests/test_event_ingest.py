"""Step 11: a system's own JSON events become traces through a closed mapping."""

import json

import httpx
import pytest
from httpx import ASGITransport

from app.event_mapping import (
    MappingError,
    events_to_envelopes,
    group_by_trace,
    load_mapping,
    read_jsonl,
    trace_uuid,
)
from app.main import app
from app.ops import event_ingest as cli

MAPPING = """
version: 1
event: event
time: {field: ts, unit: s}
trace: {key: job_id, name: support.job, session: conv}
spans:
  - start: job.started
    end: job.completed
    name: job
    type: agent
    status_field: status
    attributes: {observer.outcome: outcome}
  - start: tool.started
    end: tool.completed
    key: call_id
    parent: parent
    name_field: tool
    type: tool
    attributes: [tool.name]
    content: {input: args, output: result}
  - end: cache.hit
    name: cache
    duration_field: duration_ms
"""

T = 1_790_000_000.0


def _events(job="job-1"):
    return [
        {"event": "job.started", "job_id": job, "ts": T, "conv": "c1", "secret": "PRIVATE"},
        {"event": "tool.started", "job_id": job, "ts": T + 1, "call_id": "a", "parent": "#0",
         "tool": "search", "tool.name": "search", "args": {"q": "hello"}},
        {"event": "tool.completed", "job_id": job, "ts": T + 2, "call_id": "a", "result": "ok",
         "secret": "PRIVATE"},
        {"event": "cache.hit", "job_id": job, "ts": T + 3, "duration_ms": 250},
        {"event": "job.completed", "job_id": job, "ts": T + 4, "status": "success",
         "outcome": "answered"},
        {"event": "heartbeat", "ts": T},
        {"event": "tool.started", "ts": T},  # no job id
        {"event": "job.started", "job_id": "job-2", "ts": "yesterday"},
        "not an object",
    ]


def test_the_mapping_is_closed():
    with pytest.raises(MappingError, match="surprise: Extra inputs"):
        load_mapping(MAPPING + "\nsurprise: 1\n")
    with pytest.raises(MappingError, match="two span specs"):
        load_mapping("""event: e
time: {field: ts}
trace: {key: k}
spans: [{end: x}, {start: x, end: y}]
""")
    with pytest.raises(MappingError, match="YAML"):
        load_mapping("event: [unclosed")


def test_events_become_one_trace_with_parents_and_only_mapped_fields():
    envelopes, report = events_to_envelopes(load_mapping(MAPPING), _events(), project_id="p")
    assert report.as_dict() | {} == {
        "events": 9, "used": 5, "ignored": 1, "no_trace_key": 1, "bad_time": 1,
        "not_an_object": 1, "unfinished": 0, "traces": 1, "spans": 3}
    (env,) = envelopes
    assert env["session_id"] == "c1" and env["turn_id"] == "job-1"
    assert env["trace"]["name"] == "support.job" and env["trace"]["status"] == "ok"
    spans = {s["name"]: s for s in env["spans"]}
    job, tool, cache = spans["job"], spans["search"], spans["cache"]
    assert tool["parent_id"] == job["id"] and job["parent_id"] is None
    assert (tool["start_time"], tool["end_time"]) == (T + 1, T + 2)
    assert cache["end_time"] - cache["start_time"] == pytest.approx(0.25)  # from duration_ms
    assert job["attributes"] == {"observer.outcome": "answered"}
    assert tool["attributes"] == {"tool.name": "search"}
    assert tool["input"] == {"value": {"q": "hello"}} and tool["output"] == {"value": "ok"}
    assert "PRIVATE" not in json.dumps(envelopes)  # a field the mapping does not name


def test_ids_and_keys_are_deterministic_and_a_uuid_key_is_the_trace_id():
    mapping = load_mapping(MAPPING)
    first, _ = events_to_envelopes(mapping, _events(), project_id="p")
    again, _ = events_to_envelopes(mapping, _events(), project_id="p")
    assert first == again
    other, _ = events_to_envelopes(mapping, _events(), project_id="q")
    assert other[0]["trace"]["id"] != first[0]["trace"]["id"]  # projects never collide
    uid = "5b0f1e9c-2a36-4c7d-8f11-3e2b9d6a4c50"
    assert trace_uuid("p", uid) == uid


def test_a_step_that_never_ended_is_a_partial_span():
    events = [{"event": "job.started", "job_id": "j", "ts": T}]
    (env,), report = events_to_envelopes(load_mapping(MAPPING), events, project_id="p")
    assert report.unfinished == 1
    assert env["spans"][0]["end_time"] is None and env["spans"][0]["status"] == "unset"


def test_batches_never_split_a_trace():
    events = [{"job_id": f"j{i // 3}", "n": i} for i in range(10)]
    batches = group_by_trace(load_mapping(MAPPING), events, max_events=4)
    assert [[e["n"] for e in b] for b in batches] == [[0, 1, 2], [3, 4, 5], [6, 7, 8, 9]]
    big = [{"job_id": "big", "n": i} for i in range(6)] + [{"job_id": "small", "n": 6}]
    assert [len(b) for b in group_by_trace(load_mapping(MAPPING), big, max_events=4)] == [6, 1]


def test_jsonl_skips_blank_lines_and_counts_bad_ones():
    events, bad = read_jsonl(['{"a": 1}\n', "\n", "not json\n", b'{"b": 2}'])
    assert events == [{"a": 1}, {"b": 2}] and bad == 1


def _body(**extra):
    return {"project_id": "ev", "environment": "test", "mapping": MAPPING, "events": _events(),
            **extra}


@pytest.mark.anyio
async def test_the_route_stores_once_and_a_second_send_is_a_duplicate(client):
    first = (await client.post("/v1/ingest/events", json=_body())).json()
    assert (first["accepted"], first["duplicates"], first["rejected"]) == (1, 0, 0)
    again = (await client.post("/v1/ingest/events", json=_body())).json()
    assert (again["accepted"], again["duplicates"]) == (0, 1)
    tid = trace_uuid("ev", "job-1")
    spans = (await client.get(f"/v1/traces/{tid}/spans")).json()
    assert len(spans) == 3
    assert "PRIVATE" not in json.dumps(spans)
    tool = next(s for s in spans if s["name"] == "search")
    assert tool["input"] is None  # content follows the capture mode: metadata by default


@pytest.mark.anyio
async def test_dry_run_stores_nothing_and_a_bad_mapping_is_422(client):
    dry = (await client.post("/v1/ingest/events", json=_body(dry_run=True))).json()
    assert dry["dry_run"] and dry["traces"][0]["spans"] == 3 and dry["accepted"] == 0
    assert (await client.get(f"/v1/traces/{trace_uuid('ev', 'job-1')}")).status_code == 404
    bad = await client.post("/v1/ingest/events", json=_body(mapping="event: e\n"))
    assert bad.status_code == 422 and "mapping" in bad.text


def _files(tmp_path, events=None, mapping=MAPPING):
    (tmp_path / "map.yaml").write_text(mapping)
    lines = [json.dumps(e) for e in (events or _events()[:5])] + ["", "{broken"]
    (tmp_path / "ev.jsonl").write_text("\n".join(lines))
    return ["--mapping", str(tmp_path / "map.yaml"), "--from", str(tmp_path / "ev.jsonl"),
            "--project", "cli"]


def test_cli_dry_run_prints_the_traces_and_sends_nothing(tmp_path, capsys):
    def refuse(request):
        raise AssertionError("dry run must not send")

    assert cli.main(_files(tmp_path) + ["--dry-run"], transport=httpx.MockTransport(refuse)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] and out["bad_lines"] == 1
    assert out["traces"][0]["span_names"] == ["cache", "job", "search"]


def test_cli_exit_codes_for_a_bad_mapping_and_an_unreachable_observer(tmp_path, capsys):
    assert cli.main(_files(tmp_path, mapping="spans: []\n")) == 2
    assert "mapping" in json.loads(capsys.readouterr().out)["error"]

    def down(request):
        raise httpx.ConnectError("refused")

    assert cli.main(_files(tmp_path), transport=httpx.MockTransport(down)) == 2
    assert "unreachable" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.anyio
async def test_cli_send_stores_through_the_route(tmp_path, client):
    args = cli.build_parser().parse_args(_files(tmp_path))
    mapping_text = (tmp_path / "map.yaml").read_text()
    events, _ = cli.JsonlSource(args.source).read()
    report = await cli.send(mapping_text, load_mapping(mapping_text), events, url="http://test",
                            api_key="", project="cli", environment="test", batch_size=2,
                            transport=ASGITransport(app=app))
    assert (report["accepted"], report["rejected"]) == (1, 0)
    assert report["batches"] == 1  # five events of one job travel together despite batch_size 2
    assert len((await client.get(f"/v1/traces/{trace_uuid('cli', 'job-1')}/spans")).json()) == 3
