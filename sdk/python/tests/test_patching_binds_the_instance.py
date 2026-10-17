"""Audit 2026-10-17: assigning a bound method as a class attribute lost `self` of the patched
class, so every instrumented
`client.chat.completions.create(...)` raised TypeError. The patch must be a plain function that
receives the instance."""

from llm_observatory.instrumentors.openai import _as_method, _unwrap


class FakeCompletions:
    def create(self, *, model, messages):
        return {"id": "r1", "model": model, "n": len(messages), "inst": self}


def test_patched_create_receives_the_instance_and_calls_the_original():
    original = FakeCompletions.create
    seen = {}

    def patched(inst, *args, **kwargs):
        seen["inst"] = inst
        return original(inst, *args, **kwargs)

    FakeCompletions.create = _as_method(patched, original)
    completions = FakeCompletions()
    result = completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
    assert result["id"] == "r1" and result["inst"] is completions and seen["inst"] is completions
    # patching twice keeps the real original reachable
    FakeCompletions.create = _as_method(patched, FakeCompletions.create)
    assert _unwrap(FakeCompletions.create) is original
    FakeCompletions.create = original
