"""OTLPExporter: retry classification, backoff, persistent spool and stats."""

import json
import stat

import httpx
import pytest

from llm_observatory.exporters.otlp import OTLPExporter, backoff_delay


def _exporter(handler, tmp_path=None, **kw):
    ex = OTLPExporter(
        endpoint="http://backend.test",
        spool_dir=tmp_path,
        sleep=lambda s: ex_sleeps.append(s),
        **kw,
    )
    ex._client = httpx.Client(transport=httpx.MockTransport(handler))
    return ex


ex_sleeps: list[float] = []


@pytest.fixture(autouse=True)
def _reset_sleeps():
    ex_sleeps.clear()


def _spans(n=2, prefix="s"):
    return [{"id": f"{prefix}{i}", "trace_id": "t"} for i in range(n)]


def test_backoff_grows_is_jittered_capped_and_honours_retry_after():
    for attempt, ceiling in ((0, 0.5), (1, 1.0), (2, 2.0), (10, 10.0)):
        d = backoff_delay(attempt, 0.5, 10.0)
        assert ceiling / 2 <= d <= ceiling
    assert backoff_delay(0, 0.5, 10.0, retry_after=7.0) >= 7.0
    assert backoff_delay(0, 0.5, 10.0, retry_after=999.0) == 10.0  # capped


def test_transient_failures_are_retried_with_waits_and_then_succeed():
    calls = iter([503, 429, 200])
    ex = _exporter(lambda r: httpx.Response(next(calls)))
    ex.export(_spans(3))
    stats = ex.stats()
    assert stats["exported"] == 3 and stats["retried"] == 2 and stats["dropped"] == 0
    assert len(ex_sleeps) == 2 and ex_sleeps[1] >= ex_sleeps[0] / 2


def test_a_permanent_4xx_is_not_retried_and_counts_as_rejected():
    seen = []
    ex = _exporter(lambda r: seen.append(1) or httpx.Response(422))
    ex.export(_spans())
    assert len(seen) == 1 and ex_sleeps == []
    assert ex.stats()["rejected"] == 2 and ex.stats()["exported"] == 0


def test_without_a_spool_an_exhausted_batch_is_dropped_and_counted():
    ex = _exporter(lambda r: httpx.Response(500), max_retries=3)
    ex.export(_spans(4))
    stats = ex.stats()
    assert stats["dropped"] == 4 and stats["pending"] == 0 and stats["retried"] == 2


def test_spool_keeps_failed_batches_and_recovers_them_in_order(tmp_path):
    up = {"value": False}
    sent: list[list[str]] = []

    def handler(request):
        if not up["value"]:
            return httpx.Response(503)
        sent.append([s["id"] for s in json.loads(request.content)["spans"]])
        return httpx.Response(200)

    ex = _exporter(handler, tmp_path, max_retries=2)
    ex.export(_spans(2, "a"))
    ex.export(_spans(1, "b"))
    stats = ex.stats()
    assert stats["spooled"] == 3 and stats["pending"] == 3 and stats["dropped"] == 0
    files = sorted(tmp_path.glob("batch-*.json"))
    assert len(files) == 2 and stat.S_IMODE(files[0].stat().st_mode) == 0o600

    up["value"] = True  # the backend is back: the next export drains the spool, oldest first
    ex.export(_spans(1, "c"))
    assert sent == [["c0"], ["a0", "a1"], ["b0"]]
    stats = ex.stats()
    assert stats["recovered"] == 3 and stats["pending"] == 0 and not list(tmp_path.glob("*.json"))


def test_the_spool_survives_a_restart(tmp_path):
    down = _exporter(lambda r: httpx.Response(503), tmp_path, max_retries=1)
    down.export(_spans(2))
    assert down.stats()["pending"] == 2
    sent = []
    fresh = _exporter(lambda r: sent.append(r) or httpx.Response(200), tmp_path)
    assert fresh.stats()["pending"] == 2  # a new process sees the old batch
    fresh.flush()  # flushing (e.g. on shutdown) tries the spool
    assert len(sent) == 1 and fresh.stats()["recovered"] == 2


def test_a_permanently_rejected_spooled_batch_is_discarded_not_retried_forever(tmp_path):
    down = _exporter(lambda r: httpx.Response(503), tmp_path, max_retries=1)
    down.export(_spans(1))
    fresh = _exporter(lambda r: httpx.Response(400), tmp_path)
    fresh.flush()
    assert fresh.stats()["rejected"] == 1 and fresh.stats()["pending"] == 0


def test_the_spool_is_bounded_and_evicts_the_oldest(tmp_path):
    ex = _exporter(lambda r: httpx.Response(503), tmp_path, max_retries=1, spool_max_bytes=250)
    for i in range(6):
        ex.export([{"id": f"x{i}", "trace_id": "t", "pad": "p" * 60}])
    stats = ex.stats()
    assert stats["spool_evicted"] >= 1 and stats["pending"] >= 1
    kept = [json.loads(p.read_text())[0]["id"] for p in sorted(tmp_path.glob("batch-*.json"))]
    assert kept[-1] == "x5"  # the newest survives
    assert sum(p.stat().st_size for p in tmp_path.glob("*.json")) <= 250 + 150


def test_network_errors_are_retried_too():
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200)

    ex = _exporter(handler, max_retries=3)
    ex.export(_spans())
    assert ex.stats()["exported"] == 2 and len(attempts) == 3
