"""The agent span contract (observer.agent.v1): keys are metadata, types map from every path."""

import re
from pathlib import Path

import pytest

from app import agent_contract
from app.capture import filter_attributes
from app.metrics_core import METRICS, span_signal_query
from app.metrics_query import VIEWS
from app.otlp import _span_type


def test_contract_keys_survive_metadata_mode():
    keys = set(agent_contract.TURN_KEYS)
    for group in agent_contract.KEYS.values():
        keys |= group
    kept = filter_attributes({k: "x" for k in keys}, "metadata")
    assert set(kept) == keys, sorted(keys - set(kept))


def test_otlp_observer_span_type_maps_to_contract():
    assert _span_type({"observer.span.type": "gate"}) == "gate"
    assert _span_type({"observer.span.type": "Retrieval"}) == "retrieval"
    assert _span_type({"observer.span.type": "made-up", "gen_ai.operation.name": "chat"}) == "llm"
    # OpenInference retrievers keep their stored type and count as the contract's retrieval
    assert _span_type({"openinference.span.kind": "RETRIEVER"}) == "retriever"
    assert agent_contract.contract_type("retriever", {}) == "retrieval"


def test_reason_codes_accept_lists_and_strings():
    assert agent_contract.codes("a, b,,c") == ["a", "b", "c"]
    assert agent_contract.codes(["x", 3]) == ["x", "3"]
    assert agent_contract.codes(None) == []


def test_signals_never_select_content_columns():
    sql = str(span_signal_query(["t"]))
    assert "spans.input" not in sql and "spans.output" not in sql


def _english_keys() -> set[str]:
    text = (Path(__file__).resolve().parents[2] / "dashboard/src/i18n-en.ts").read_text("utf-8")
    quoted = re.findall(r"^\s*'((?:[^'\\]|\\.)*)'\s*:", text, re.M)
    quoted += re.findall(r'^\s*"((?:[^"\\]|\\.)*)"\s*:', text, re.M)
    bare = re.findall(r"^\s*([A-Za-zÁÉÍÓÚáéíóúñÑü_][\wÁÉÍÓÚáéíóúñÑü]*)\s*:", text, re.M)
    return {q.replace("\\'", "'") for q in quoted} | set(bare)


def test_backend_labels_have_english_entries():
    english = _english_keys()
    labels = {m["label"] for m in METRICS.values()}
    for view in VIEWS.values():
        labels.add(view.label)
        labels |= set(view.measures.values()) | set(view.dimensions.values())
        labels |= set(view.filter_only.values())
    assert not sorted(labels - english), sorted(labels - english)


@pytest.mark.anyio
async def test_contract_route(client):
    data = (await client.get("/v1/agent/contract")).json()
    assert data["version"] == "observer.agent.v1"
    assert "abstained" in data["outcomes"] and "gate" in data["span_types"]
    assert "gate.reason_codes" in data["keys"]["gate"]
