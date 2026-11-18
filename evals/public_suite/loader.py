"""Fixed-seed, label-stratified samples from public labeled datasets (OpenSpec 2026-11-11).

    uv run python -m evals.public_suite.loader --tier b      # HF_TOKEN required for tier a

Sample text is cached under artifacts/ and never committed; ids and SHA-256 go in manifest.json.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq  # type: ignore[import-untyped]

SEED = 20261111
CACHE_DIR = Path("artifacts/public_suite")
MANIFEST = Path(__file__).with_name("manifest.json")


class PublicSuiteError(RuntimeError):
    """Raised when a public tier cannot be fetched, sampled or verified."""


@dataclass(frozen=True)
class Sample:
    id: str
    locale: str
    source: str
    claim: str
    supported: bool
    subset: str


def _assin2(row: dict[str, Any]) -> Sample | None:
    return Sample(
        id=f"assin2-{row['sentence_pair_id']}",
        locale="pt-BR",
        source=str(row["premise"]),
        claim=str(row["hypothesis"]),
        supported=int(row["entailment_judgment"]) == 1,
        subset="assin2",
    )


AGGREFACT_SUBSETS = ("RAGTruth", "Reveal")


def _aggrefact(row: dict[str, Any]) -> Sample | None:
    subset = str(row["dataset"])
    if subset not in AGGREFACT_SUBSETS:
        return None
    digest = hashlib.sha1(f"{subset}|{row['doc']}|{row['claim']}".encode()).hexdigest()[:12]  # noqa: S324
    return Sample(
        id=f"aggrefact-{subset}-{digest}",
        locale="en-US",
        source=str(row["doc"]),
        claim=str(row["claim"]),
        supported=int(row["label"]) == 1,
        subset=subset,
    )


@dataclass(frozen=True)
class Tier:
    dataset: str
    split: str
    size: int
    gated: bool
    mapper: Callable[[dict[str, Any]], Sample | None]


TIERS = {
    "a": Tier("lytang/LLM-AggreFact", "test", 150, gated=True, mapper=_aggrefact),
    "b": Tier("nilc-nlp/assin2", "test", 150, gated=False, mapper=_assin2),
}


def map_rows(tier: Tier, rows: Iterable[dict[str, Any]]) -> list[Sample]:
    return [sample for row in rows if (sample := tier.mapper(row)) is not None]


def stratified_sample(samples: list[Sample], *, size: int, seed: int) -> list[Sample]:
    """Equal draws from every (subset, label) group; same rows and seed give the same ids."""
    groups: dict[tuple[str, bool], list[Sample]] = defaultdict(list)
    for sample in samples:
        groups[(sample.subset, sample.supported)].append(sample)
    keys = sorted(groups)
    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not security
    chosen: list[Sample] = []
    for position, key in enumerate(keys):
        wanted = size // len(keys) + (1 if position < size % len(keys) else 0)
        group = sorted(groups[key], key=lambda s: s.id)
        if len(group) < wanted:
            msg = f"group {key} has only {len(group)} rows, needs {wanted}"
            raise PublicSuiteError(msg)
        chosen.extend(rng.sample(group, wanted))
    return sorted(chosen, key=lambda s: s.id)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def write_cache(
    tier: str, samples: list[Sample], *, cache: Path, manifest: Path, seed: int
) -> None:
    cache.parent.mkdir(parents=True, exist_ok=True)
    lines = (json.dumps(asdict(s), ensure_ascii=False) for s in samples)
    cache.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    entries = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    entries[tier] = {
        "dataset": TIERS[tier].dataset,
        "split": TIERS[tier].split,
        "seed": seed,
        "size": len(samples),
        "sha256": _sha256(cache),
        "ids": [s.id for s in samples],
    }
    manifest.write_text(json.dumps(entries, indent=1) + "\n", encoding="utf-8", newline="\n")


def load_cached(tier: str, *, cache: Path | None = None, manifest: Path = MANIFEST) -> list[Sample]:
    """Load a cached tier only if it matches the committed manifest."""
    cache = cache or CACHE_DIR / f"tier_{tier}.jsonl"
    if not cache.exists():
        command = f"uv run python -m evals.public_suite.loader --tier {tier}"
        msg = f"tier {tier} is not cached; run: {command}"
        raise PublicSuiteError(msg)
    expected = json.loads(manifest.read_text(encoding="utf-8"))[tier]["sha256"]
    if _sha256(cache) != expected:
        msg = f"tier {tier} cache SHA-256 does not match the manifest"
        raise PublicSuiteError(msg)
    rows = cache.read_text(encoding="utf-8").splitlines()
    return [Sample(**json.loads(line)) for line in rows if line]


def _get(url: str, token: str | None) -> bytes:
    headers = {"User-Agent": "macy-benchmark/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)  # noqa: S310
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        data: bytes = response.read()
        return data


def fetch_tier(tier: str, *, seed: int = SEED) -> list[Sample]:
    """Download the split's Parquet export, sample it and write the cache and manifest."""
    spec = TIERS[tier]
    token = os.environ.get("HF_TOKEN")
    if spec.gated and not token:
        msg = f"{spec.dataset} is gated: accept its terms on Hugging Face and set HF_TOKEN"
        raise PublicSuiteError(msg)
    query = urllib.parse.urlencode({"dataset": spec.dataset})
    listing = json.loads(_get(f"https://datasets-server.huggingface.co/parquet?{query}", token))
    urls = [f["url"] for f in listing.get("parquet_files", []) if f["split"] == spec.split]
    if not urls:
        msg = f"no Parquet files for {spec.dataset} split {spec.split}"
        raise PublicSuiteError(msg)
    rows = [row for url in urls for row in pq.read_table(io.BytesIO(_get(url, token))).to_pylist()]
    samples = stratified_sample(map_rows(spec, rows), size=spec.size, seed=seed)
    write_cache(tier, samples, cache=CACHE_DIR / f"tier_{tier}.jsonl", manifest=MANIFEST, seed=seed)
    return samples


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=sorted(TIERS), required=True)
    samples = fetch_tier(parser.parse_args().tier)
    print(f"cached {len(samples)} samples, {sum(s.supported for s in samples)} supported")


if __name__ == "__main__":
    main()
