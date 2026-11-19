"""Evaluate the detection pipeline against a hand-labelled ground truth.

    python eval/evaluate.py                       # regex only + default model
    python eval/evaluate.py qwen2.5:3b qwen3:8b   # compare models (must be pulled in Ollama)

Two views of quality:

- Entity level (strict): a detected value counts only if it matches a labelled value exactly.
  Splitting "Calle Mayor 1, 28013 Madrid" into two pieces counts as a miss plus two false positives.
- Character level: the share of personal-data characters that end up masked (recall), and
  the share of masked characters that really were personal data (precision). This is what
  matters for an anonymizer: whether the data is hidden, not how it was split.

Each model runs once as a warm-up and is then timed over --runs runs.
"""

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.anonymizer import anonymize  # noqa: E402
from app.config import Settings  # noqa: E402
from app.detectors.llm import OllamaDetector, _entity_pattern  # noqa: E402
from app.detectors.regex import detect_regex  # noqa: E402
from app.pdf import extract_pages  # noqa: E402

DATASET = json.loads((Path(__file__).parent / "ground_truth.json").read_text(encoding="utf-8"))


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def load(doc: dict) -> str:
    path = ROOT / doc["file"]
    if path.suffix == ".pdf":
        return "\n".join(extract_pages(path.read_bytes(), max_pages=50))
    return path.read_text(encoding="utf-8")


def char_set(spans) -> set[int]:
    return {i for start, end in spans for i in range(start, end)}


async def run_once(text: str, model: str | None):
    started = time.perf_counter()
    spans = detect_regex(text)
    if model:
        llm_spans, _ = await OllamaDetector(Settings(ollama_model=model, _env_file=None)).detect(text)
        spans += llm_spans
    result = anonymize(text, spans)
    return result, time.perf_counter() - started


async def evaluate(model: str | None, runs: int) -> dict:
    tp = fp = fn = 0
    gt_chars = masked_chars = hit_chars = 0
    times: list[float] = []
    misses: list[str] = []
    extras: list[str] = []
    for doc in DATASET:
        text = load(doc)
        expected = {norm(v) for v in doc["entities"]}
        truth = char_set(m.span() for v in doc["entities"] for m in _entity_pattern(v).finditer(text))
        if model:
            await run_once(text, model)  # warm-up: load the model into memory
        for _ in range(runs):
            result, elapsed = await run_once(text, model)
            times.append(elapsed)
        # quality from the last run (temperature 0, so runs are deterministic)
        found = {norm(v) for v in result.mapping.values()}
        masked = char_set((r.start, r.end) for r in result.replacements)
        tp += len(found & expected)
        fp += len(found - expected)
        fn += len(expected - found)
        misses += sorted(expected - found)
        extras += sorted(found - expected)
        gt_chars += len(truth)
        masked_chars += len(masked)
        hit_chars += len(truth & masked)

    def ratio(a: int, b: int) -> float:
        return a / b if b else 0.0

    p, r = ratio(tp, tp + fp), ratio(tp, tp + fn)
    return {
        "model": model or "regex only",
        "precision": p,
        "recall": r,
        "f1": ratio(2 * p * r, p + r) if p + r else 0.0,
        "char_precision": ratio(hit_chars, masked_chars),
        "char_recall": ratio(hit_chars, gt_chars),
        "seconds": statistics.median(times),
        "missed": misses,
        "false_positives": extras,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("models", nargs="*", default=[Settings(_env_file=None).ollama_model])
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()

    results = [await evaluate(None, args.runs)]
    for model in args.models:
        print(f"evaluating {model}…", file=sys.stderr)
        results.append(await evaluate(model, args.runs))

    n_docs = len(DATASET)
    n_ents = sum(len(d["entities"]) for d in DATASET)
    print(f"{n_docs} documents · {n_ents} labelled entities\n")
    print("| Detector | Entity P | Entity R | Entity F1 | PII chars masked | Masked chars that were PII | Time / doc |")
    print("|---|---|---|---|---|---|---|")
    for r in results:
        t = f"{r['seconds'] * 1000:.0f} ms" if r["seconds"] < 1 else f"{r['seconds']:.1f} s"
        print(
            f"| `{r['model']}` | {r['precision']:.0%} | {r['recall']:.0%} | {r['f1']:.2f} "
            f"| **{r['char_recall']:.1%}** | {r['char_precision']:.1%} | {t} |"
        )
    for r in results:
        if r["missed"] or r["false_positives"]:
            print(f"\n{r['model']}: missed {r['missed']} · extra {r['false_positives']}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
