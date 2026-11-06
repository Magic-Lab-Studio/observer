"""A1 of the 2026-10-18 evaluation: ISO 8601 timestamps read the same on Python 3.10 and later.

The backend supports Python 3.10, whose ``datetime.fromisoformat`` refuses ``Z``, a fraction of
other than 3 or 6 digits, ``+0000`` and the basic format; 3.11 accepts them. A span sent with one
of those was a 422 on 3.10 only. CI runs these tests on 3.10 as well.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.ingest_validation import parse_iso8601, parse_timestamp

NOON = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc).timestamp()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-10-18T12:00:00Z", NOON),
        ("2026-10-18T12:00:00z", NOON),
        ("2026-10-18t12:00:00+00:00", NOON),
        ("2026-10-18 12:00:00+0000", NOON),  # +hhmm
        ("2026-10-18T07:00:00-05", NOON),  # +hh
        ("2026-10-18T14:00:00+02:00", NOON),
        ("2026-10-18T12:00Z", NOON),  # no seconds
        ("20261018T120000Z", NOON),  # basic format
        ("2026-10-18T12:00:00.5Z", NOON + 0.5),  # one fraction digit
        ("2026-10-18T12:00:00,25Z", NOON + 0.25),  # comma
        ("2026-10-18T12:00:00.123456789Z", NOON + 0.123456),  # nanoseconds, cut
        (" 2026-10-18T12:00:00.12Z ", NOON + 0.12),
    ],
)
def test_iso_forms_python_3_10_refused_are_read(text, expected):
    assert parse_timestamp(text) == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize(
    "text",
    ["2026-10-18T12:00:00", "2026-10-18", "18/10/2026 12:00Z", "2026-10-18T12:00:00+25:00",
     "2026-13-01T00:00:00Z", "2026-10-18T12:00:00Zjunk", "yesterday"],
)
def test_bad_or_naive_iso_strings_are_rejected(text):
    with pytest.raises(ValueError):
        parse_timestamp(text)


def test_offset_and_naive_values():
    assert parse_iso8601("2026-10-18T12:00:00-0530").utcoffset() == -timedelta(hours=5, minutes=30)
    assert parse_iso8601("2026-10-18T12:00:00").tzinfo is None


@pytest.mark.anyio
async def test_a_span_with_a_short_fraction_and_z_is_ingested(client):
    span = {"id": "a1-span", "trace_id": "a1-trace", "name": "step", "status": "ok",
            "start_time": "2026-10-18T12:00:00.5Z", "end_time": "2026-10-18T12:00:01.75+0000"}
    response = await client.post("/v1/traces/batch", json={"spans": [span]})
    assert response.status_code == 200, response.text
    trace = (await client.get("/v1/traces/a1-trace")).json()
    assert trace["start_time"].startswith("2026-10-18T12:00:00.5")
