"""Defects found by the system audit of 2026-10-17
(manitos/docs/auditoria_sistema_2026_10_17.md)."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.capture import apply_capture
from app.models.evaluation import Evaluation
from app.models.trace import Trace

pytestmark = pytest.mark.anyio


async def test_deleting_a_trace_removes_its_evaluations(client, db_session):
    """SQLite ran without PRAGMA foreign_keys, so DELETE /v1/traces/{id} left orphan evaluations
    that still counted in /summary."""
    trace_id = "11111111-1111-1111-1111-111111111111"
    trace = Trace(id=trace_id, name="t", start_time=datetime.now(timezone.utc))
    db_session.add(trace)
    await db_session.flush()
    db_session.add(Evaluation(trace_id=trace.id, evaluator_type="rule", score=1.0))
    await db_session.commit()
    response = await client.delete(f"/v1/traces/{trace.id}")
    assert response.status_code == 200
    query = select(Evaluation).where(Evaluation.trace_id == trace.id)
    rows = (await db_session.execute(query)).scalars().all()
    assert rows == []


def test_metadata_mode_redacts_free_form_metadata():
    meta_in = {"prompt": "mail me@x.io", "turn_id": "t1"}
    _, _, _, meta = apply_capture(
        "metadata", inp={"q": "x"}, out=None, attributes={"model": "m"}, meta=meta_in
    )
    assert meta == {"prompt": "mail [REDACTED:email]", "turn_id": "t1"}
