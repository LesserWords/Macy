"""GEO / AI readiness checks: robots AI bots, llms.txt, SSR body, landmarks, JSON-LD."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse

from engine import safe_http
from engine.audit.page_checks import ParsedHTML, parse_html
from engine.contracts import AuditCheckResult
from engine.production.schema import validate_schema

AI_SEARCH_BOTS: tuple[str, ...] = ("OAI-SearchBot", "PerplexityBot", "ChatGPT-User")
AI_TRAINING_BOTS: tuple[str, ...] = ("GPTBot", "ClaudeBot", "Google-Extended", "CCBot")
MIN_SSR_WORDS_ERROR: int = 50
MIN_SSR_WORDS_WARNING: int = 150


def _apply_robots_directive(
    line: str,
    current_agents: list[str],
    groups: dict[str, list[tuple[str, str]]],
) -> list[str]:
    key, val = (part.strip() for part in line.split(":", 1))
    low_key = key.lower()
    if low_key == "user-agent":
        agent = val.lower()
        groups.setdefault(agent, [])
        return [*current_agents, agent]
    if low_key in {"allow", "disallow"} and current_agents:
        for agent in current_agents:
            groups.setdefault(agent, []).append((low_key, val))
    return current_agents


def _parse_robots_groups(robots_txt: str) -> dict[str, list[tuple[str, str]]]:
    groups: dict[str, list[tuple[str, str]]] = {}
    current_agents: list[str] = []
    for raw_line in robots_txt.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            current_agents = []
            continue
        if ":" in line:
            current_agents = _apply_robots_directive(line, current_agents, groups)
    return groups


def _is_path_allowed(rules: list[tuple[str, str]], path: str) -> bool:
    best_len = -1
    allowed = True
    for rule_type, pattern in rules:
        if not pattern:
            continue
        if path.startswith(pattern) and len(pattern) >= best_len:
            best_len = len(pattern)
            allowed = rule_type == "allow"
    return allowed


def is_url_allowed_by_robots(robots_txt: str, path: str, user_agent: str = "*") -> bool:
    """Return True if path is permitted under robots.txt rules for user_agent or wildcard '*'."""
    groups = _parse_robots_groups(robots_txt)
    rules = groups.get(user_agent.lower())
    effective = rules if rules is not None else groups.get("*", [])
    return _is_path_allowed(effective, path)


def parse_robots_ai_bots(robots_txt: str, *, target_path: str = "/") -> dict[str, bool]:
    """Evaluate robots.txt Allow/Disallow policies for AI search and training bots."""
    groups = _parse_robots_groups(robots_txt)
    wildcard_rules = groups.get("*", [])
    status: dict[str, bool] = {}
    for bot in (*AI_SEARCH_BOTS, *AI_TRAINING_BOTS):
        bot_rules = groups.get(bot.lower())
        effective = bot_rules if bot_rules is not None else wildcard_rules
        status[bot] = _is_path_allowed(effective, target_path)
    return status


def _safe_fetch_text(url: str, *, timeout: float) -> tuple[int, str]:
    try:
        resp = safe_http.safe_request("GET", url, timeout=timeout, max_bytes=262_144)
        return resp.status_code, resp.text
    except (safe_http.SafeHTTPError, urllib.error.URLError, OSError, TimeoutError):
        return 0, ""


def fetch_robots_and_llms(page_url: str, *, timeout: float = 5.0) -> tuple[str, bool]:
    """Fetch /robots.txt and probe /llms.txt (or /llms-full.txt) on target origin."""
    parsed_url = urllib.parse.urlparse(page_url)
    origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
    robots_code, robots_body = _safe_fetch_text(f"{origin}/robots.txt", timeout=timeout)
    robots_txt = robots_body if 200 <= robots_code < 300 else ""

    has_llms = False
    for suffix in ("/llms.txt", "/llms-full.txt"):
        code, body = _safe_fetch_text(f"{origin}{suffix}", timeout=timeout)
        if 200 <= code < 300 and body.strip():
            has_llms = True
            break
    return robots_txt, has_llms


def _check_jsonld(blocks: list[str]) -> tuple[AuditCheckResult, list[str]]:
    if not blocks:
        return (
            AuditCheckResult(
                check="structured_data",
                category="ai",
                status="warning",
                score=0.5,
                detail="No JSON-LD structured data found.",
                recommendation="Add Schema.org JSON-LD (e.g. Article, Product, FAQPage).",
            ),
            [],
        )
    found_types: list[str] = []
    errors: list[str] = []
    for raw in blocks:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"Invalid JSON-LD: {exc.msg}")
            continue
        res = validate_schema(payload)
        found_types.extend(res.types)
        errors.extend(res.errors)

    if errors:
        return (
            AuditCheckResult(
                check="structured_data",
                category="ai",
                status="warning",
                score=0.5,
                detail=f"JSON-LD issues: {'; '.join(errors[:2])}",
                recommendation="Fix missing required Schema.org fields in JSON-LD.",
            ),
            found_types,
        )
    return (
        AuditCheckResult(
            check="structured_data",
            category="ai",
            status="pass",
            score=1.0,
            detail=f"Valid JSON-LD types detected: {', '.join(found_types)}.",
            metadata={"types": found_types},
        ),
        found_types,
    )


def _check_ssr_and_landmarks(doc: ParsedHTML) -> list[AuditCheckResult]:
    words = doc.word_count
    if words < MIN_SSR_WORDS_ERROR:
        ssr_res = AuditCheckResult(
            check="ssr_content",
            category="ai",
            status="error",
            score=0.0,
            detail=f"Initial SSR HTML has only {words} words (JS-only rendering suspected).",
            recommendation="Server-render core editorial content so AI & search bots can read it.",
        )
    elif words < MIN_SSR_WORDS_WARNING:
        ssr_res = AuditCheckResult(
            check="ssr_content",
            category="ai",
            status="warning",
            score=0.5,
            detail=f"Initial SSR HTML is thin ({words} words).",
            recommendation="Expand substantive server-rendered copy (>= 150 words).",
        )
    else:
        ssr_res = AuditCheckResult(
            check="ssr_content",
            category="ai",
            status="pass",
            score=1.0,
            detail=f"Substantive SSR text present ({words} words).",
        )

    has_main = bool(doc.landmarks & {"main", "article"})
    landmark_res = AuditCheckResult(
        check="semantic_landmarks",
        category="ai",
        status="pass" if has_main else "warning",
        score=1.0 if has_main else 0.5,
        detail=(
            f"Semantic landmarks found: {sorted(doc.landmarks)}."
            if has_main
            else "Missing <main> or <article> semantic landmarks."
        ),
        recommendation=None if has_main else "Wrap core page content in <main> and <article>.",
    )
    return [ssr_res, landmark_res]


def _resolve_robots_and_llms(
    url: str,
    robots_txt: str | None,
    has_llms_txt: bool | None,
    timeout: float,
) -> tuple[str, bool]:
    if robots_txt is not None and has_llms_txt is not None:
        return robots_txt, has_llms_txt
    fetched_robots, fetched_llms = fetch_robots_and_llms(url, timeout=timeout)
    return (
        fetched_robots if robots_txt is None else robots_txt,
        fetched_llms if has_llms_txt is None else has_llms_txt,
    )


def _build_bot_policy_checks(
    url: str,
    eff_robots: str,
    eff_llms: bool,
) -> list[AuditCheckResult]:
    path = urllib.parse.urlparse(url).path or "/"
    bot_policy = parse_robots_ai_bots(eff_robots, target_path=path)
    blocked_search = [b for b in AI_SEARCH_BOTS if not bot_policy[b]]
    blocked_train = [b for b in AI_TRAINING_BOTS if not bot_policy[b]]

    ai_search_res = AuditCheckResult(
        check="ai_search_bots",
        category="ai",
        status="error" if blocked_search else "pass",
        score=0.0 if blocked_search else 1.0,
        detail=(
            f"AI search crawlers blocked in robots.txt: {', '.join(blocked_search)}."
            if blocked_search
            else "AI search crawlers (OAI-SearchBot, PerplexityBot) allowed."
        ),
        recommendation=(
            f"Allow {', '.join(blocked_search)} in robots.txt for AI citation visibility."
            if blocked_search
            else None
        ),
        metadata={"bot_policy": bot_policy},
    )
    ai_train_res = AuditCheckResult(
        check="ai_training_bots",
        category="ai",
        status="info",
        score=0.0,
        detail=(
            f"Training crawlers blocked: {', '.join(blocked_train)}."
            if blocked_train
            else "AI training crawlers permitted by robots.txt."
        ),
        metadata={"blocked_training_bots": blocked_train},
    )
    llms_res = AuditCheckResult(
        check="llms_txt",
        category="ai",
        status="pass" if eff_llms else "warning",
        score=1.0 if eff_llms else 0.5,
        detail="/llms.txt present on origin." if eff_llms else "No /llms.txt guide found.",
        recommendation=None if eff_llms else "Publish /llms.txt summarizing key site URLs.",
    )
    return [ai_search_res, ai_train_res, llms_res]


def run_ai_checks(  # noqa: PLR0913
    url: str,
    html_text: str,
    *,
    parsed: ParsedHTML | None = None,
    robots_txt: str | None = None,
    has_llms_txt: bool | None = None,
    timeout: float = 5.0,
) -> tuple[list[AuditCheckResult], list[str]]:
    """Run GEO / AI search readiness checks on a fetched page."""
    doc = parsed or parse_html(html_text)
    eff_robots, eff_llms = _resolve_robots_and_llms(url, robots_txt, has_llms_txt, timeout)
    ld_res, ld_types = _check_jsonld(doc.json_ld_blocks)
    return [
        *_build_bot_policy_checks(url, eff_robots, eff_llms),
        *_check_ssr_and_landmarks(doc),
        ld_res,
    ], ld_types
