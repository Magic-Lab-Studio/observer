"""Compare Meta models on one writing agent, observed by Observer.

The same agent writes the same poems with each model:

* Llama, Meta's open-weights model, served locally by Ollama (``llama3.2:3b``);
* Muse Spark through the Meta Model API (``muse-spark-1.3``; needs ``META_API_KEY``).

The poems form a small collection ("poemario") in a neurotic, cursed and frantic voice. Every
case names a word the poem must contain. Each run is traced with the Observer SDK and reported as
a dataset run, so the dashboard shows latency, tokens and errors per model and Observer checks
every case (no error, the required word is there). ``--judge`` then asks Observer's LLM judge to
grade every trace (start the backend with ``OBSERVATORY_JUDGE_PROVIDER=meta``). The poems are
also written to a text file (``--out``) to be read later.

    python examples/meta/compare.py --observer http://127.0.0.1:8000 \
        --models llama3.2:3b muse-spark-1.3 --out poemario.txt
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import llm_observatory as obs
from openai import OpenAI

PROJECT = "meta-compare"
DATASET = "poemario maldito"
META_BASE_URL = "https://api.meta.ai/v1"

SYSTEM = (
    "Eres un poeta maldito, neurótico y frenético. Escribes en español, en verso libre, con "
    "imágenes urbanas y nocturnas, obsesiones que vuelven, frases que se atropellan y ninguna "
    "moraleja. Respondes solo con el poema: primera línea, el título; después, entre 10 y 16 "
    "versos. Nada de explicaciones."
)

# (title for the collection, prompt, word the poem must contain)
CASES = [
    ("Insomnio", "Un poema sobre el insomnio a las 4 de la mañana.", "reloj"),
    ("La ciudad", "Un poema sobre la ciudad que no deja dormir.", "neón"),
    ("Café", "Un poema sobre la décima taza de café.", "temblor"),
    ("El espejo", "Un poema sobre mirarse al espejo y no reconocerse.", "espejo"),
    ("Deuda", "Un poema sobre deber dinero y deberse a uno mismo.", "deuda"),
    ("Pantalla", "Un poema sobre una pantalla que nunca se apaga.", "pantalla"),
    ("La máquina", "Un poema sobre una máquina que escribe poemas de noche.", "máquina"),
    ("Final", "Un poema sobre el último tren y no subirse.", "tren"),
]


def client_for(model: str) -> tuple[OpenAI, dict]:
    """The OpenAI client that reaches ``model`` and the token-cap argument that endpoint takes."""
    if model.startswith("muse-"):
        key = os.getenv("META_API_KEY") or os.getenv("MODEL_API_KEY")
        if not key:
            sys.exit("META_API_KEY is not set (Meta Model API key from https://dev.meta.ai)")
        # Muse Spark reasons before it writes and those tokens count against the cap
        return OpenAI(base_url=META_BASE_URL, api_key=key, max_retries=2), {
            "max_completion_tokens": ARGS.max_tokens + 1100, "reasoning_effort": "low"}
    base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    return OpenAI(base_url=base, api_key="ollama"), {"max_tokens": ARGS.max_tokens}


@obs.trace(name="poet", span_type="agent")
def poet(client: OpenAI, model: str, cap: dict, prompt: str, word: str) -> tuple[str, str]:
    root = obs.current_span()
    request = f"{prompt} Usa la palabra «{word}»."
    root.set_input({"prompt": request})
    root.set_attribute("poet.model", model)
    reply = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": request}],
        temperature=0.9,
        **cap,
    )
    poem = (reply.choices[0].message.content or "").strip()
    root.set_output({"content": poem})
    return root.trace_id, poem


def dataset(api: httpx.Client) -> tuple[str, list[str]]:
    """The dataset of these cases, created once and reused."""
    for found in api.get("/v1/datasets", params={"project_id": PROJECT}).json():
        if found["name"] == DATASET:
            items = api.get(f"/v1/datasets/{found['id']}").json()["items"]
            by_intent = {i["intent"]: i["id"] for i in items}
            return found["id"], [by_intent[f"poema-{n}"] for n in range(len(CASES))]
    ds = api.post("/v1/datasets", json={"name": DATASET, "project_id": PROJECT}).json()["id"]
    ids = []
    for n, (_, _, word) in enumerate(CASES):
        item = api.post(f"/v1/datasets/{ds}/items", json={
            "intent": f"poema-{n}",
            "expect": {"health": {"no_error": True}, "text": {"must_contain": [word]}},
        })
        item.raise_for_status()
        ids.append(item.json()["id"])
    return ds, ids


def main() -> None:
    tracer = obs.Tracer(service_name=PROJECT, environment="demo")
    tracer.add_exporter(obs.OTLPExporter(endpoint=ARGS.observer))
    obs.set_tracer(tracer)
    obs.instrument(openai=True)
    api = httpx.Client(base_url=ARGS.observer, timeout=120)

    out = Path(ARGS.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    traces: dict[str, list[str]] = {}
    with out.open("a", encoding="utf-8") as book:
        book.write(f"\n{'=' * 60}\nPOEMARIO MALDITO · {datetime.now():%Y-%m-%d %H:%M}\n{'=' * 60}\n")
        for model in ARGS.models:
            client, cap = client_for(model)
            traces[model] = []
            book.write(f"\n\n######## {model} ########\n")
            for title, prompt, word in CASES:
                started = time.monotonic()
                trace_id, poem = poet(client, model, cap, prompt, word)
                took = time.monotonic() - started
                has_word = word.casefold() in poem.casefold()
                print(f"{model:>16}  {took:5.1f}s  {'ok ' if has_word else 'BAD'}  {title}")
                book.write(f"\n--- {title} · «{word}» · {took:.1f} s · traza {trace_id[:8]} ---\n")
                book.write(poem + "\n")
                book.flush()
                traces[model].append(trace_id)
    tracer.shutdown()
    time.sleep(1)  # the backend stores the last batch
    print(f"poemario: {out}")

    ds, items = dataset(api)
    for model, trace_ids in traces.items():
        run = api.post(f"/v1/datasets/{ds}/runs", json={
            "name": model,
            "version": {"model": model, "config": {"max_tokens": ARGS.max_tokens}},
            "results": [{"item_id": i, "trace_id": t} for i, t in zip(items, trace_ids)],
        })
        run.raise_for_status()
        print(f"run {model}: pass rate {run.json()['summary'].get('pass_rate')}")
        if ARGS.judge:
            for trace_id in trace_ids:
                graded = api.post("/v1/evaluations/run", json={
                    "trace_id": trace_id, "evaluator_type": "llm_judge", "criteria": []})
                graded.raise_for_status()
                details = graded.json()["result"]["details"]
                print(f"  judge {trace_id[:8]}: score {graded.json()['score']} "
                      f"{details.get('judge')} {details['evaluation_status']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--observer", default="http://127.0.0.1:8000")
    parser.add_argument("--models", nargs="+", default=["llama3.2:3b", "muse-spark-1.3"])
    parser.add_argument("--max-tokens", type=int, default=400)
    parser.add_argument("--out", default="poemario.txt")
    parser.add_argument("--judge", action="store_true")
    ARGS = parser.parse_args()
    main()
