"""Planted-error benchmark comparing a plain verify prompt with Macy's checkers.

    OPENROUTER_API_KEY=... uv run python -m evals.claim_benchmark.harness \
        --model anthropic/claude-haiku-5.5 --limit 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import unicodedata
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from functools import partial
from pathlib import Path
from types import EllipsisType
from typing import Any, Literal, Protocol

import yaml
from pydantic import BaseModel, ValidationError

from engine.contracts import ContentDraft, DraftClaim
from engine.integrations.openrouter import OpenRouterLLM
from engine.integrations.ports import LLMPort
from engine.llm import _extract_cost_and_tokens
from engine.quality import checks
from engine.quality.evaluate import evaluate
from evals.claim_benchmark import BenchmarkDataError, rules
from evals.claim_benchmark.jev import ClaimDecision, DecisionBatch, JevClient
from evals.public_suite.loader import MANIFEST, TIERS, Sample, load_cached

CASES_PATH = Path(__file__).with_name("cases.yaml")
HOLDOUT_PATH = Path(__file__).with_name("holdout") / "cases.yaml"
OUTPUT_DIR = Path("artifacts/claim_benchmark")
MATCH_THRESHOLD = 0.6
DOC_ID = "00000000-0000-4000-8000-000000000001"
REFERENCE_TIME = datetime(2026, 11, 10, tzinfo=UTC)
META = {
    "en-US": "A sourced summary of this industry update, covering the reported figures, "
    "who said what, and what comes next for the sector.",
    "pt-BR": "Resumo com fontes desta novidade do setor, com os números divulgados, "
    "quem afirmou o quê e os próximos passos para o mercado.",
}
JSON_ATTEMPTS = 3
CASCADE_THRESHOLD = 0.70  # Fitted in BM-4 on development data (docs/plans/BM.md).
DATASETS = {"dev": (CASES_PATH, rules.DEV_SHA256), "holdout": (HOLDOUT_PATH, rules.HOLDOUT_SHA256)}


@dataclass(frozen=True)
class PlantedError:
    type: str
    sentence_index: int


@dataclass(frozen=True)
class Variant:
    id: str
    case_id: str
    locale: str
    title: str
    source: str
    sentences: tuple[str, ...]
    error: PlantedError | None = None
    extra_errors: tuple[PlantedError, ...] = ()

    @property
    def body(self) -> str:
        return " ".join(self.sentences)

    @property
    def errors(self) -> tuple[PlantedError, ...]:
        return (self.error, *self.extra_errors) if self.error else ()


@dataclass
class ClaimTrace:
    """One judged claim; every Jev metric is derived from these."""

    text: str
    jev_label: str | None
    jev_confidence: float
    p_supported: float
    numbers_ok: bool
    accepted_by_jev: bool
    escalated: bool
    supported: bool
    truly_supported: bool
    evidence: str


@dataclass
class MethodResult:
    publish: bool
    # None means the method only gives an article-level answer.
    flagged: list[str] | None = None
    traces: list[ClaimTrace] = field(default_factory=list)


@dataclass
class Score:
    errors_total: int = 0
    errors_caught: int = 0
    clean_articles: int = 0
    clean_rejected: int = 0
    dirty_articles: int = 0
    false_passes: int = 0
    clean_sentences: int = 0
    false_alarms: int = 0
    failed_runs: int = 0
    single_articles: int = 0
    single_detected: int = 0
    multi_articles: int = 0
    multi_detected: int = 0
    by_type: dict[str, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))

    @property
    def catch_rate(self) -> float:
        return _ratio(self.errors_caught, self.errors_total)

    @property
    def false_pass_rate(self) -> float:
        return _ratio(self.false_passes, self.dirty_articles)

    @property
    def clean_reject_rate(self) -> float:
        return _ratio(self.clean_rejected, self.clean_articles)

    @property
    def false_alarm_rate(self) -> float:
        return _ratio(self.false_alarms, self.clean_sentences)


def _ratio(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def _replace_once(text: str, find: str, replace: str, case_id: str) -> str:
    if text.count(find) != 1:
        msg = f"{case_id}: {find!r} must appear exactly once in {text!r}"
        raise BenchmarkDataError(msg)
    return text.replace(find, replace)


def build_variants(case: dict[str, Any]) -> list[Variant]:
    """Return the clean article plus one article per planted error."""
    base = [s for s in case["sentences"] if "planted_sentence" not in s]
    make = _variant_factory(case)
    variants = [make("clean", tuple(s["text"] for s in base), None)]
    for index, sentence in enumerate(base):
        for number, err in enumerate(sentence.get("errors", [])):
            texts = [s["text"] for s in base]
            texts[index] = _replace_once(texts[index], err["find"], err["replace"], case["id"])
            planted = PlantedError(err["type"], index)
            variants.append(make(f"s{index}e{number}", tuple(texts), planted))
    variants.extend(_planted_sentence_variants(case, make))
    variants.extend(_combination_variants(case, base, make))
    return variants


def _combination_variants(
    case: dict[str, Any], base: list[dict[str, Any]], make: Callable[..., Variant]
) -> list[Variant]:
    """Articles with several planted errors, listed as error ids such as "s0e1"."""
    variants = []
    for number, refs in enumerate(case.get("combinations", [])):
        texts = [s["text"] for s in base]
        planted: list[PlantedError] = []
        for ref in refs:
            index, err_number = (int(part) for part in ref[1:].split("e"))
            if any(p.sentence_index == index for p in planted):
                msg = f"{case['id']}: combination {refs} uses sentence {index} twice"
                raise BenchmarkDataError(msg)
            err = base[index]["errors"][err_number]
            texts[index] = _replace_once(texts[index], err["find"], err["replace"], case["id"])
            planted.append(PlantedError(err["type"], index))
        sentences = tuple(texts)
        variants.append(make(f"combo{number}", sentences, planted[0], tuple(planted[1:])))
    return variants


def composition_problems(variants: list[Variant]) -> list[str]:
    """Every way a dataset falls short of the frozen holdout composition (D4)."""
    cases = {v.case_id: v for v in variants}
    per_locale = {loc: sum(v.locale == loc for v in cases.values()) for loc in ("en-US", "pt-BR")}
    errors = {
        (v.case_id, e.sentence_index, v.sentences[e.sentence_index])
        for v in variants
        for e in v.errors
    }
    clean = sum(not v.errors for v in variants)
    multi_share = sum(len(v.errors) > 1 for v in variants) / len(variants) if variants else 0.0
    words = statistics.median(len(v.source.split()) for v in cases.values()) if cases else 0
    checks = [
        ("articles", len(cases), rules.D4_MIN_ARTICLES),
        ("en-US articles", per_locale["en-US"], rules.D4_MIN_PER_LANGUAGE),
        ("pt-BR articles", per_locale["pt-BR"], rules.D4_MIN_PER_LANGUAGE),
        ("planted errors", len(errors), rules.D4_MIN_ERRORS),
        ("clean articles", clean, rules.D4_MIN_CLEAN),
        ("multi-error share", multi_share, rules.D4_MIN_MULTI_ERROR_SHARE),
        ("median source words", words, rules.D4_MIN_MEDIAN_SOURCE_WORDS),
    ]
    return [f"{name}: {value} < {minimum}" for name, value, minimum in checks if value < minimum]


def _variant_factory(case: dict[str, Any]) -> Callable[..., Variant]:
    def make(
        suffix: str,
        sentences: tuple[str, ...],
        error: PlantedError | None,
        extra: tuple[PlantedError, ...] = (),
    ) -> Variant:
        return Variant(
            id=f"{case['id']}/{suffix}",
            case_id=case["id"],
            locale=case["locale"],
            title=case["title"],
            source=case["source"].strip(),
            sentences=sentences,
            error=error,
            extra_errors=extra,
        )

    return make


def _planted_sentence_variants(case: dict[str, Any], make: Callable[..., Variant]) -> list[Variant]:
    variants = []
    for position, sentence in enumerate(case["sentences"]):
        if "planted_sentence" not in sentence:
            continue
        kept = case["sentences"][:position] + case["sentences"][position + 1 :]
        before = [s["text"] for s in kept[:position] if "planted_sentence" not in s]
        after = [s["text"] for s in kept[position:] if "planted_sentence" not in s]
        planted = PlantedError(sentence["planted_sentence"], len(before))
        texts = (*before, sentence["text"], *after)
        variants.append(make(f"insert{position}", texts, planted))
    return variants


def load_variants(path: Path = CASES_PATH) -> list[Variant]:
    cases = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [variant for case in cases for variant in build_variants(case)]


def dataset_sha256(path: Path) -> str:
    """Hash with CRLF normalized, so Windows and Unix checkouts agree."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_dataset(
    name: Literal["dev", "holdout"],
    *,
    path: Path | None = None,
    expected_sha256: str | None | EllipsisType = ...,
) -> list[Variant]:
    """Load a dataset only if it matches the hash frozen in rules.py."""
    default_path, frozen = DATASETS[name]
    path = path or default_path
    expected = frozen if isinstance(expected_sha256, EllipsisType) else expected_sha256
    if expected is None:
        msg = f"{name} dataset is not frozen: set its hash in rules.py first"
        raise BenchmarkDataError(msg)
    actual = dataset_sha256(path)
    if actual != expected:
        msg = f"{name} dataset SHA-256 {actual} does not match frozen {expected}"
        raise BenchmarkDataError(msg)
    variants = load_variants(path)
    if name == "holdout" and (problems := composition_problems(variants)):
        msg = f"holdout composition falls short: {'; '.join(problems)}"
        raise BenchmarkDataError(msg)
    return variants


def public_variants(samples: list[Sample]) -> list[Variant]:
    """Each public (source, claim) pair becomes a one-sentence article."""
    return [
        Variant(
            id=s.id,
            case_id=s.id,
            locale=s.locale,
            title="",
            source=s.source,
            sentences=(s.claim,),
            error=None if s.supported else PlantedError("unsupported", 0),
        )
        for s in samples
    ]


def _load_for_run(name: str) -> tuple[list[Variant], str | None]:
    """Variants for a run and the dataset hash recorded in its report."""
    if name.startswith("public-"):
        tier = name.removeprefix("public-")
        frozen = json.loads(MANIFEST.read_text(encoding="utf-8"))[tier]["sha256"]
        return public_variants(load_cached(tier)), str(frozen)
    dataset: Literal["dev", "holdout"] = "dev" if name == "dev" else "holdout"
    return load_dataset(dataset), DATASETS[dataset][1]


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)  # noqa: S603, S607
    return result.stdout.strip()


def _normalize(text: str) -> str:
    cleaned = unicodedata.normalize("NFKC", text).casefold()
    cleaned = re.sub(r"[\"'“”‘’]", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _canonical_number(raw: str) -> str:
    parts = re.split(r"[.,]", raw)
    if len(parts) == 1 or len(parts[-1]) == 3:  # noqa: PLR2004
        return "".join(parts)
    decimals = parts[-1].rstrip("0")
    whole = "".join(parts[:-1])
    return f"{whole}.{decimals}" if decimals else whole


def numbers_in(text: str) -> set[str]:
    """Return numbers in text, treating 1.234,5 and 1,234.5 as the same value."""
    return {_canonical_number(found) for found in re.findall(r"\d+(?:[.,]\d+)*", text)}


def guard_status(claim: str, status: str, evidence: str, source: str) -> str:
    """Downgrade a 'supported' answer whose evidence is not checkable in code."""
    if status != "supported":
        return status
    if not evidence.strip() or _normalize(evidence) not in _normalize(source):
        return "unverifiable"
    return "supported" if numbers_in(claim) <= numbers_in(evidence) else "unverifiable"


def _sentence_for(quote: str, sentences: Iterable[str]) -> int | None:
    needle = _normalize(quote)
    best_index: int | None = None
    best_score = 0.0
    for index, sentence in enumerate(sentences):
        hay = _normalize(sentence)
        similar = SequenceMatcher(None, needle, hay).ratio()
        score = 1.0 if needle and (needle in hay or hay in needle) else similar
        if score > best_score:
            best_index, best_score = index, score
    return best_index if best_score >= MATCH_THRESHOLD else None


def _flagged_sentences(variant: Variant, result: MethodResult) -> set[int]:
    if result.flagged is None:
        return set()
    found = (_sentence_for(quote, variant.sentences) for quote in result.flagged)
    return {index for index in found if index is not None}


def _score_dirty(score: Score, variant: Variant, result: MethodResult, flagged: set[int]) -> None:
    score.dirty_articles += 1
    score.false_passes += int(result.publish)
    if len(variant.errors) > 1:
        score.multi_articles += 1
        score.multi_detected += int(not result.publish)
    else:
        score.single_articles += 1
        score.single_detected += int(not result.publish)
    for error in variant.errors:
        caught = not result.publish if result.flagged is None else error.sentence_index in flagged
        score.errors_total += 1
        score.errors_caught += int(caught)
        tally = score.by_type[error.type]
        tally[0] += int(caught)
        tally[1] += 1


def score_results(rows: Iterable[tuple[Variant, MethodResult | None]]) -> Score:
    """Compute benchmark rates; a None result is a failed run and is excluded."""
    score = Score()
    for variant, result in rows:
        if result is None:
            score.failed_runs += 1
            continue
        flagged = _flagged_sentences(variant, result)
        wrong = {error.sentence_index for error in variant.errors}
        if result.flagged is not None:
            score.clean_sentences += len(variant.sentences) - len(wrong)
            score.false_alarms += len(flagged - wrong)
        if not variant.errors:
            score.clean_articles += 1
            score.clean_rejected += int(not result.publish)
        else:
            _score_dirty(score, variant, result, flagged)
    return score


class Problem(BaseModel):
    quote: str
    reason: str


class PlainReport(BaseModel):
    verdict: Literal["publish", "revise"]
    problems: list[Problem]


class ClaimList(BaseModel):
    claims: list[str]


class ClaimJudgment(BaseModel):
    status: Literal["supported", "contradicted", "unverifiable"]
    evidence_quote: str


PLAIN_PROMPT = """You are a careful fact-checker for a content team.
Check the article against the source. Every factual statement must be supported by the source.
Report each statement that is wrong, exaggerated, misattributed or not in the source,
quoting the exact sentence from the article. Do not report opinions or advice.
Answer "publish" only if there are no problems, otherwise "revise".

SOURCE:
{source}

ARTICLE:
{article}
"""

EXTRACT_PROMPT = """List every checkable factual claim in this article, one per sentence,
copied verbatim from the article. Exclude opinions, advice and predictions by the writer.

ARTICLE:
{article}
"""

JUDGE_PROMPT = """Decide whether the source backs this claim. Read every detail: numbers, dates,
names, roles, direction of change, scope and stated causes.
supported: the source states it. contradicted: the source says otherwise.
unverifiable: the source does not say it.
If supported, copy the sentence from the source that proves it, verbatim, as evidence_quote.
Otherwise evidence_quote is the closest sentence, or empty.

SOURCE:
{source}

CLAIM:
{claim}
"""


class TunedFinding(BaseModel):
    quote: str
    kind: Literal["contradicted", "unsupported", "not_checkable"]
    reason: str


class PlainTunedReport(BaseModel):
    findings: list[TunedFinding]


class ExtractedClaim(BaseModel):
    text: str
    kind: Literal["fact", "attributed", "advice", "opinion"]


class ClaimListV2(BaseModel):
    claims: list[ExtractedClaim]


CHECKABLE_RULES = """A sentence is checkable when it states a number, date, name, role,
event, comparison, cause or scope, or attributes a plan, forecast, expectation or statement to a
person, company, organization, survey or report. Attributed statements are checkable even when
they are about the future: the source must say that this party said or expects it.
A sentence is not checkable when it is the writer's own advice, recommendation or opinion
addressed to readers."""

SUPPORT_RULES = """Treat the source as the only truth and assume a statement is unsupported
until the source shows it. A statement is supported only if the source states it with the same
numbers, dates, names, roles, direction of change, scope and cause. It is contradicted if the
source says something incompatible, and unsupported if the source does not say it."""

PLAIN_TUNED_PROMPT = f"""You check an article against its source for a content team.

{CHECKABLE_RULES}

{SUPPORT_RULES}

List every article sentence that is contradicted, unsupported or not checkable (advice or
opinion), copying the article sentence exactly as quote. Do not list supported sentences.

SOURCE:
{{source}}

ARTICLE:
{{article}}
"""

EXTRACT_PROMPT_V2 = f"""Copy every sentence of this article verbatim, one per item, and classify it.

{CHECKABLE_RULES}

kind is "fact" for checkable statements by the writer, "attributed" for checkable statements
attributed to someone (including their plans and forecasts), "advice" for the writer's
recommendations, and "opinion" for the writer's own judgments.

ARTICLE:
{{article}}
"""

JUDGE_PROMPT_V2 = f"""Decide whether the source backs this claim.

{SUPPORT_RULES}

supported: the source states it. contradicted: the source says otherwise.
unverifiable: the source does not say it.
If supported, copy the sentence from the source that proves it, verbatim, as evidence_quote.
Otherwise evidence_quote is the closest sentence, or empty.

SOURCE:
{{source}}

CLAIM:
{{claim}}
"""


class MeteredLLM:
    """Calls OpenRouter in JSON mode and totals its reported cost.

    Stays independent of Instructor so every method is measured through the same call.
    """

    def __init__(self, inner: Any, model: str, reasoning_effort: str | None = None) -> None:
        self._inner = inner
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._lock = threading.Lock()
        self.cost_usd = 0.0
        self.models: set[str] = set()

    def healthcheck(self) -> None:
        self._inner.healthcheck()

    def complete(self, prompt: str, schema: type[Any] | None = None) -> dict[str, Any]:
        if schema is None:
            msg = "MeteredLLM requires a response schema"
            raise ValueError(msg)
        content = (
            f"{prompt}\n\nReply with only a JSON object matching this JSON Schema:\n"
            f"{json.dumps(schema.model_json_schema())}"
        )
        body: dict[str, Any] = {"provider": {"require_parameters": True, "data_collection": "deny"}}
        if self._reasoning_effort:
            body["reasoning_effort"] = self._reasoning_effort
        last_error: ValidationError | None = None
        for _ in range(JSON_ATTEMPTS):
            response = self._inner.client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": content}],
                response_format={"type": "json_object"},
                extra_body=body,
            )
            with self._lock:
                self.cost_usd += float(_extract_cost_and_tokens(response)[0])
                self.models.add(str(getattr(response, "model", "") or self._model))
            text = re.sub(
                r"^```(?:json)?\s*|\s*```$", "", response.choices[0].message.content or ""
            ).strip()
            if not text:
                continue
            try:
                result: dict[str, Any] = schema.model_validate_json(text).model_dump()
            except ValidationError as exc:
                last_error = exc
                continue
            return result
        assert last_error is not None  # noqa: S101
        raise last_error


def plain_prompt(llm: LLMPort, variant: Variant) -> MethodResult:
    prompt = PLAIN_PROMPT.format(source=variant.source, article=variant.body)
    report = PlainReport.model_validate(llm.complete(prompt, PlainReport))
    return MethodResult(
        publish=report.verdict == "publish" and not report.problems,
        flagged=[problem.quote for problem in report.problems],
    )


def per_claim(llm: LLMPort, variant: Variant) -> MethodResult:
    extracted = ClaimList.model_validate(
        llm.complete(EXTRACT_PROMPT.format(article=variant.body), ClaimList)
    )
    flagged = _judge_claims(llm, extracted.claims, variant.source, JUDGE_PROMPT)
    return MethodResult(publish=not flagged, flagged=flagged)


def _judge_one(llm: LLMPort, claim: str, source: str, template: str) -> tuple[bool, str]:
    """Return whether the judge and quote guard support the claim, and the verified quote."""
    judged = ClaimJudgment.model_validate(
        llm.complete(template.format(source=source, claim=claim), ClaimJudgment)
    )
    supported = guard_status(claim, judged.status, judged.evidence_quote, source) == "supported"
    return supported, judged.evidence_quote if supported else ""


def _judge_claims(llm: LLMPort, claims: Iterable[str], source: str, template: str) -> list[str]:
    """Return the claims the judge and quote guard do not support."""
    return [claim for claim in claims if not _judge_one(llm, claim, source, template)[0]]


def plain_tuned(llm: LLMPort, variant: Variant) -> MethodResult:
    """One prompt given a fair chance; the code, not the model, decides what counts."""
    prompt = PLAIN_TUNED_PROMPT.format(source=variant.source, article=variant.body)
    report = PlainTunedReport.model_validate(llm.complete(prompt, PlainTunedReport))
    flagged = [f.quote for f in report.findings if f.kind != "not_checkable"]
    return MethodResult(publish=not flagged, flagged=flagged)


def _extract_checkable(llm: LLMPort, variant: Variant) -> list[str]:
    extracted = ClaimListV2.model_validate(
        llm.complete(EXTRACT_PROMPT_V2.format(article=variant.body), ClaimListV2)
    )
    return [c.text for c in extracted.claims if c.kind in {"fact", "attributed"}]


def per_claim_v2(llm: LLMPort, variant: Variant) -> MethodResult:
    flagged = _judge_claims(llm, _extract_checkable(llm, variant), variant.source, JUDGE_PROMPT_V2)
    return MethodResult(publish=not flagged, flagged=flagged)


class ClaimJudge(Protocol):
    def judge_claims(self, source: str, claims: list[str]) -> DecisionBatch: ...


class MeteredJev:
    """Totals Jev cost and resolved models across worker threads."""

    def __init__(self, inner: ClaimJudge) -> None:
        self._inner = inner
        self._lock = threading.Lock()
        self.cost_usd = 0.0
        self.models: set[str] = set()

    def judge_claims(self, source: str, claims: list[str]) -> DecisionBatch:
        batch = self._inner.judge_claims(source, claims)
        with self._lock:
            self.cost_usd += batch.cost_usd
            self.models |= batch.models
        return batch


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", _normalize(text)))


def evidence_passage(claim: str, source: str) -> str:
    """Source sentence sharing the most words with the claim, ties broken by shared numbers."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", source) if s.strip()]
    words, numbers = _words(claim), numbers_in(claim)
    return max(sentences, key=lambda s: (len(words & _words(s)), len(numbers & numbers_in(s))))


def _truly_supported(variant: Variant, claim: str) -> bool:
    wrong = {error.sentence_index for error in variant.errors}
    return not wrong or _sentence_for(claim, variant.sentences) not in wrong


def _p_supported(decision: ClaimDecision) -> float:
    fallback = decision.confidence if decision.label == "supported" else 0.0
    return float(decision.probabilities.get("supported", fallback))


def _with_traces(traces: list[ClaimTrace]) -> MethodResult:
    flagged = [t.text for t in traces if not t.supported]
    return MethodResult(publish=not flagged, flagged=flagged, traces=traces)


def _jev_decisions(
    llm: LLMPort, variant: Variant, jev: ClaimJudge
) -> list[tuple[str, ClaimDecision, bool]]:
    claims = _extract_checkable(llm, variant)
    batch = jev.judge_claims(variant.source, claims)
    source_numbers = numbers_in(variant.source)
    return [
        (claim, decision, numbers_in(claim) <= source_numbers)
        for claim, decision in zip(claims, batch.decisions, strict=True)
    ]


def jev_judge(llm: LLMPort, variant: Variant, *, jev: ClaimJudge) -> MethodResult:
    """Jev alone judges every extracted claim; no LLM judge."""
    traces = []
    for claim, decision, numbers_ok in _jev_decisions(llm, variant, jev):
        supported = decision.label == "supported"
        traces.append(
            ClaimTrace(
                text=claim,
                jev_label=decision.label,
                jev_confidence=decision.confidence,
                p_supported=_p_supported(decision),
                numbers_ok=numbers_ok,
                accepted_by_jev=supported,
                escalated=False,
                supported=supported,
                truly_supported=_truly_supported(variant, claim),
                evidence=evidence_passage(claim, variant.source) if supported else "",
            )
        )
    return _with_traces(traces)


def jev_cascade(
    llm: LLMPort, variant: Variant, *, jev: ClaimJudge, threshold: float
) -> MethodResult:
    """Accept confident, number-checked Jev support; send every other claim to the LLM judge."""
    traces = []
    for claim, decision, numbers_ok in _jev_decisions(llm, variant, jev):
        accept = decision.label == "supported" and decision.confidence >= threshold and numbers_ok
        if accept:
            supported, evidence = True, evidence_passage(claim, variant.source)
        else:
            supported, evidence = _judge_one(llm, claim, variant.source, JUDGE_PROMPT_V2)
        traces.append(
            ClaimTrace(
                text=claim,
                jev_label=decision.label,
                jev_confidence=decision.confidence,
                p_supported=_p_supported(decision),
                numbers_ok=numbers_ok,
                accepted_by_jev=accept,
                escalated=not accept,
                supported=supported,
                truly_supported=_truly_supported(variant, claim),
                evidence=evidence,
            )
        )
    return _with_traces(traces)


def select_threshold(
    points: Iterable[tuple[float, bool]],
    grid: Iterable[float] = rules.THRESHOLD_GRID,
    min_precision: float = rules.W6_MIN_ACCEPT_PRECISION,
) -> float | None:
    """Lowest grid value whose accepted (confidence, correct) points reach the precision bar."""
    points = list(points)
    for threshold in grid:
        kept = [correct for confidence, correct in points if confidence >= threshold]
        if kept and sum(kept) / len(kept) >= min_precision:
            return threshold
    return None


def reliability_table(points: Iterable[tuple[float, bool]]) -> list[dict[str, Any]]:
    """Mean predicted probability vs observed frequency, in bins of width 0.1."""
    bins: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for probability, observed in points:
        bins[min(int(probability * 10), 9)].append((probability, observed))
    return [
        {
            "bin": f"{index / 10:.1f}-{(index + 1) / 10:.1f}",
            "count": len(items),
            "mean_p": sum(p for p, _ in items) / len(items),
            "observed": sum(o for _, o in items) / len(items),
        }
        for index, items in sorted(bins.items())
    ]


def rogan_gladen(*, observed: float, tpr: float, tnr: float) -> float | None:
    """Prevalence corrected for judge error: (p + TNR - 1) / (TPR + TNR - 1), clipped to [0, 1]."""
    denominator = tpr + tnr - 1
    if denominator <= 0:
        return None
    return min(max((observed + tnr - 1) / denominator, 0.0), 1.0)


def _share(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def detection_rates(score: Score) -> dict[str, float | None]:
    """Article-level TPR by error count, TNR on clean articles and sentences, Rogan-Gladen."""
    detected = score.single_detected + score.multi_detected
    articles = score.clean_articles + score.dirty_articles
    tpr = _share(detected, score.dirty_articles)
    tnr = _share(score.clean_articles - score.clean_rejected, score.clean_articles)
    observed = _share(score.clean_rejected + detected, articles)
    corrected = None
    if observed is not None and tpr is not None and tnr is not None:
        corrected = rogan_gladen(observed=observed, tpr=tpr, tnr=tnr)
    return {
        "tpr_single": _share(score.single_detected, score.single_articles),
        "tpr_multi": _share(score.multi_detected, score.multi_articles),
        "tnr_clean_articles": tnr,
        "tnr_clean_sentences": _share(
            score.clean_sentences - score.false_alarms, score.clean_sentences
        ),
        "rogan_gladen": corrected,
    }


def _rates(score: Score) -> dict[str, float | None]:
    return {
        "catch_rate": score.catch_rate,
        "false_pass_rate": score.false_pass_rate,
        "clean_reject_rate": score.clean_reject_rate,
        "false_alarm_rate": score.false_alarm_rate,
        **detection_rates(score),
    }


def method_report(
    rows: list[tuple[Variant, MethodResult | None]], *, cost_usd: float, models: set[str]
) -> dict[str, Any]:
    """Everything one method's run must record for key-result audit (D5)."""
    score = score_results(rows)
    traces = [t for _, result in rows if result for t in result.traces]
    accepted = [t.truly_supported for t in traces if t.accepted_by_jev]
    locales = sorted({variant.locale for variant, _ in rows})
    return {
        **_rates(score),
        "failed_runs": score.failed_runs,
        "usd_per_article": cost_usd / len(rows) if rows else 0.0,
        "resolved_models": sorted(models),
        "escalation_rate": sum(t.escalated for t in traces) / len(traces) if traces else 0.0,
        "jev_accept_precision": sum(accepted) / len(accepted) if accepted else None,
        "jev_reliability": reliability_table(
            (t.p_supported, t.truly_supported) for t in traces if t.jev_label is not None
        ),
        "by_locale": {
            locale: _rates(score_results((v, r) for v, r in rows if v.locale == locale))
            for locale in locales
        },
        "by_type": {k: {"caught": v[0], "total": v[1]} for k, v in score.by_type.items()},
        "rows": [
            {
                "id": variant.id,
                "error": variant.error.type if variant.error else None,
                "error_sentence": (
                    variant.sentences[variant.error.sentence_index] if variant.error else None
                ),
                "publish": result.publish if result else None,
                "flagged": result.flagged if result else None,
                "errors": [
                    {"type": e.type, "sentence": variant.sentences[e.sentence_index]}
                    for e in variant.errors
                ],
                "traces": [asdict(t) for t in result.traces] if result else [],
            }
            for variant, result in rows
        ],
    }


def _draft_and_evidence(variant: Variant) -> tuple[ContentDraft, list[dict[str, Any]]]:
    draft = ContentDraft(
        title=variant.title,
        meta_description=META[variant.locale],
        slug=variant.case_id,
        body_md=f"# {variant.title}\n\n{variant.body}",
        claims=[DraftClaim(text=s, kind="fact", source_id=DOC_ID) for s in variant.sentences],
    )
    evidence = [
        {
            "id": DOC_ID,
            "title": variant.title,
            "source_name": "benchmark",
            "source_role": "news",
            "url": f"https://example.com/{variant.case_id}",
            "published_at": "2026-11-01T00:00:00Z",
            "date_status": "verified",
            "fetch_status": "ok",
            "summary": "",
            "facts": [],
            "extracted_text": variant.source,
        }
    ]
    return draft, evidence


def macy_evaluator(llm: LLMPort, variant: Variant) -> MethodResult:
    """Today's rubric evaluator. Only factual findings count; rubric style issues are not errors."""
    draft, evidence = _draft_and_evidence(variant)
    options = checks.DeterministicRunOptions(reference_time=REFERENCE_TIME)
    gate = checks.run(draft, evidence_documents=evidence, locale=variant.locale, options=options)
    if gate.verdict != "PASS":
        msg = f"{variant.id}: deterministic checks stopped the run: {gate.findings}"
        raise BenchmarkDataError(msg)
    report = evaluate(
        draft, evidence=evidence, llm_adapter=llm, locale=variant.locale, options=options
    )
    factual = any(
        finding.criterion == "factual_accuracy" and finding.severity in {"critical", "major"}
        for finding in report.findings
    )
    return MethodResult(publish=not factual)


METHODS: dict[str, Callable[[LLMPort, Variant], MethodResult]] = {
    "plain_prompt": plain_prompt,
    "macy_evaluator": macy_evaluator,
    "per_claim": per_claim,
    "plain_tuned": plain_tuned,
    "per_claim_v2": per_claim_v2,
}


JEV_METHODS = ("jev_judge", "jev_cascade")


def _method_for(
    name: str, jev: ClaimJudge, threshold: float
) -> Callable[[LLMPort, Variant], MethodResult]:
    if name == "jev_judge":
        return partial(jev_judge, jev=jev)
    if name == "jev_cascade":
        return partial(jev_cascade, jev=jev, threshold=threshold)
    return METHODS[name]


def run_method(
    name: str,
    method: Callable[[LLMPort, Variant], MethodResult],
    llm: LLMPort,
    variants: list[Variant],
    workers: int,
) -> list[tuple[Variant, MethodResult | None]]:

    def run_one(variant: Variant) -> tuple[Variant, MethodResult | None]:
        try:
            return variant, method(llm, variant)
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED {name} {variant.id}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return variant, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run_one, variants))


def _print_summary(name: str, score: Score, cost: float) -> None:
    usd = f"${cost:.5f}"
    print(
        f"{name:15} catch {score.catch_rate:6.1%}  false-pass {score.false_pass_rate:6.1%}  "
        f"clean-rejected {score.clean_reject_rate:6.1%}  "
        f"false-alarms {score.false_alarm_rate:6.1%}  failed {score.failed_runs}  "
        f"cost/article {usd}"
    )


def _select(variants: list[Variant], locale: str | None, limit: int | None) -> list[Variant]:
    chosen = [v for v in variants if locale is None or v.locale == locale]
    if limit is None:
        return chosen
    case_ids = list(dict.fromkeys(v.case_id for v in chosen))[:limit]
    return [v for v in chosen if v.case_id in case_ids]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="anthropic/claude-haiku-5.5")
    datasets = [*DATASETS, *(f"public-{tier}" for tier in TIERS)]
    parser.add_argument("--dataset", choices=datasets, default="dev")
    parser.add_argument("--limit", type=int, help="Use only the first N source cases")
    parser.add_argument("--locale", choices=["en-US", "pt-BR"])
    parser.add_argument("--workers", type=int, default=6)
    choices = [*METHODS, *JEV_METHODS]
    parser.add_argument("--methods", nargs="+", choices=choices, default=["jev_judge"])
    parser.add_argument("--threshold", type=float, default=CASCADE_THRESHOLD)
    args = parser.parse_args(argv)

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY is not set.", file=sys.stderr)
        return 2
    loaded, dataset_hash = _load_for_run(args.dataset)
    variants = _select(loaded, args.locale, args.limit)
    run_id = uuid.uuid4().hex[:8]
    output: dict[str, Any] = {
        "model": args.model,
        "dataset": args.dataset,
        "dataset_sha256": dataset_hash,
        "git_sha": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "articles": len(variants),
        "methods": {},
    }
    for name in args.methods:
        effort = "high" if name == "macy_evaluator" else None  # matches production's setting
        llm = MeteredLLM(OpenRouterLLM(), args.model, effort)
        jev = MeteredJev(JevClient(os.environ["OPENROUTER_API_KEY"]))
        method = _method_for(name, jev, args.threshold)
        rows = run_method(name, method, llm, variants, args.workers)
        cost = llm.cost_usd + jev.cost_usd
        _print_summary(name, score_results(rows), cost / len(variants))
        output["methods"][name] = method_report(rows, cost_usd=cost, models=llm.models | jev.models)
        if name == "jev_cascade":
            output["methods"][name]["threshold"] = args.threshold
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{run_id}.json"
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
