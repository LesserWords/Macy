"""CLI entry point for content engine tasks (ADR-014)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any

from engine import capabilities, config, observability, services
from engine.contracts import CrawlConfig
from engine.integrations.config import (
    rotate_workspace_integration_keys,
    set_workspace_integration,
)
from engine.integrations.registry import list_plugins
from engine.mcp import server as mcp_server


def list_plugins_command() -> list[dict[str, str]]:
    """List available integration plugins across ports."""
    return list_plugins()


def _handle_plugins_list(args: argparse.Namespace) -> int:
    del args
    for item in list_plugins_command():
        print(f"{item['port']:<18} {item['plugin']:<15} {item['class']}")
    return 0


def _resolve_workspace_owner_id(ws: dict[str, Any]) -> uuid.UUID:
    user_id = ws.get("owner_user_id")
    if not user_id:
        msg = "Workspace has no owner"
        raise ValueError(msg)
    return uuid.UUID(str(user_id))


def _resolve_workspace_and_owner(identifier: str | None) -> tuple[uuid.UUID, uuid.UUID]:
    ws = services.resolve_workspace(workspace_identifier=identifier)
    return uuid.UUID(str(ws["workspace_id"])), _resolve_workspace_owner_id(ws)


def _handle_capabilities_list(args: argparse.Namespace) -> int:
    enabled: set[str] | None = None
    if getattr(args, "workspace", None):
        ws = services.resolve_workspace(workspace_identifier=args.workspace)
        enabled = set(services.get_enabled_capabilities(workspace_id=ws["workspace_id"]))
    for slug, manifest in capabilities.discover_capabilities().items():
        status = "enabled" if enabled is None or slug in enabled else "disabled"
        core_flag = "always" if manifest.always_enabled else "toggle"
        print(f"{slug:<22} {status:<10} {core_flag:<8} {manifest.title_msgid}")
    return 0


def _handle_capabilities_toggle(args: argparse.Namespace, *, enable: bool) -> int:
    try:
        ws = services.resolve_workspace(workspace_identifier=args.workspace)
        ws_id = uuid.UUID(str(ws["workspace_id"]))
        owner_id = (
            uuid.UUID(str(args.user_id))
            if getattr(args, "user_id", None)
            else _resolve_workspace_owner_id(ws)
        )
        slug = str(args.slug).strip()
        if not enable and slug == capabilities.CORE_CAPABILITY_SLUG:
            print(f"Error: Cannot disable core capability '{slug}'", file=sys.stderr)
            return 1
        current = services.get_enabled_capabilities(workspace_id=ws_id)
        updated = [*current, slug] if enable else [item for item in current if item != slug]
        result = services.set_enabled_capabilities(
            workspace_id=ws_id,
            user_id=owner_id,
            enabled_capabilities=updated,
        )
        print(f"Capabilities for '{ws['slug']}': {', '.join(result)}")
        return 0
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


def _handle_integrations_set(args: argparse.Namespace) -> int:
    settings = json.loads(args.settings) if args.settings else None
    secrets = json.loads(args.secrets) if args.secrets else None
    set_workspace_integration(
        conn=None,
        workspace_id=uuid.UUID(args.workspace_id),
        port=args.port,
        plugin=args.plugin,
        settings=settings,
        secrets=secrets,
        updated_by=uuid.UUID(args.user_id),
    )
    print(f"Configured port '{args.port}' with plugin '{args.plugin}'")
    return 0


def _handle_integrations_preset_list(args: argparse.Namespace) -> int:
    presets = services.list_integration_presets()
    if getattr(args, "json_output", False):
        print(json.dumps(presets))
    else:
        for preset in presets:
            print(f"{preset['name']}: {preset['description']}")
    return 0


def _handle_integrations_preset_apply(args: argparse.Namespace) -> int:
    results = services.apply_integration_preset(
        conn=None,
        workspace_id=uuid.UUID(args.workspace_id),
        user_id=uuid.UUID(args.user_id),
        preset_name=args.preset_name,
    )
    if getattr(args, "json_output", False):
        print(json.dumps(results, default=str))
    else:
        print(f"Applied preset '{args.preset_name}' across {len(results)} port(s)")
    return 0


def _handle_rotate_key(args: argparse.Namespace) -> int:
    current_keys = [k.strip() for k in args.current_keys.split(",") if k.strip()]
    ws_id = uuid.UUID(args.workspace_id) if args.workspace_id else None
    count = rotate_workspace_integration_keys(
        conn=None,
        current_keys=current_keys,
        new_key=args.new_key,
        workspace_id=ws_id,
    )
    print(f"Rotated {count} integration secret(s)")
    return 0


def _handle_create_workspace(args: argparse.Namespace) -> int:
    try:
        res = services.create_workspace(
            slug=args.slug,
            name=args.name,
            industry_pack=args.pack,
            timezone=args.timezone,
        )
        print(
            f"Created workspace '{res['slug']}' ({res['workspace_id']}) "
            f"with pack '{res['industry_pack']}' and timezone '{res['timezone']}'"
        )
        return 0
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


def _handle_add_owner(args: argparse.Namespace) -> int:
    try:
        services.add_workspace_owner(
            workspace_slug=args.workspace,
            email=args.email,
        )
        print(f"Added owner '{args.email}' to workspace '{args.workspace}'")
        return 0
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


def _handle_init(args: argparse.Namespace) -> int:
    email = args.email or ("admin@localhost" if args.non_interactive else input("Admin email: "))
    slug = args.slug or ("pocket-ws" if args.non_interactive else input("Workspace slug: "))
    name = args.name or ("Pocket Workspace" if args.non_interactive else input("Workspace name: "))
    caps = [c.strip() for c in str(args.capabilities).split(",") if c.strip()]
    result = services.bootstrap_onboarding(
        conn=None,
        email=email,
        password=args.password,
        workspace_slug=slug,
        workspace_name=name,
        industry_pack=args.pack,
        timezone=args.timezone,
        enabled_capabilities=caps,
        preset_name=args.preset,
    )
    if args.site_url:
        result["autoseed"] = services.autoseed_from_url(
            workspace_id=result["workspace"]["workspace_id"],
            user_id=result["user"]["user_id"],
            site_url=args.site_url,
        )
    if getattr(args, "json_output", False):
        print(json.dumps(result, default=str))
    else:
        ws_slug = result["workspace"]["slug"]
        user_email = result["user"]["email"]
        print(f"Initialized workspace '{ws_slug}' for {user_email}")
    return 0


def _handle_seed_brand(args: argparse.Namespace) -> int:
    try:
        saved = services.seed_brand(
            workspace_slug=args.workspace,
            fixture_path=args.fixture,
        )
        print(f"Seeded brand profile v{saved['version']} for workspace '{args.workspace}'")
        return 0
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


def _handle_seed_sources(args: argparse.Namespace) -> int:
    resolved_pack = args.pack or config.get_default_industry_pack()
    try:
        seeded = services.seed_sources(industry_pack=resolved_pack)
        print(f"Seeded {len(seeded)} shared source(s) for pack '{resolved_pack}'")
        return 0
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


def _handle_verify_sources(args: argparse.Namespace) -> int:
    resolved_pack = args.pack or config.get_default_industry_pack()
    try:
        results = services.verify_sources(
            industry_pack=resolved_pack,
            days=args.days,
            offline=args.offline,
        )
        print(f"\nSeed Source Verification Report ({resolved_pack}, last {args.days} days):\n")
        header = (
            f"{'Name':<24} {'Domain':<24} {'Mode':<12} {'Enabled':<8} "
            f"{'Records':<8} {'Dated %':<8} {'Status':<12} {'Notes'}"
        )
        print(header)
        print("-" * len(header))
        for r in results:
            note_str = str(r.get("notes") or "")
            if len(note_str) > 40:
                note_str = note_str[:37] + "..."
            print(
                f"{r['name']:<24} {r['domain']:<24} {r['fetch_mode']:<12} "
                f"{str(r['enabled']):<8} {r['records_found']:<8} {r['dated_pct']:<8} "
                f"{r['status']:<12} {note_str}"
            )
        ok_count = sum(1 for r in results if r["status"] == "ok")
        disabled_count = sum(1 for r in results if r["status"] == "disabled")
        no_rec_count = sum(1 for r in results if r["status"] == "no_records")
        error_count = sum(1 for r in results if r["status"] == "error")
        print("\n" + "=" * 80)
        print(
            f"Total: {len(results)} | Active OK: {ok_count} | Disabled: {disabled_count} | "
            f"No Records: {no_rec_count} | Errors: {error_count}"
        )
        return 0 if error_count == 0 else 1
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


def _handle_run_week(args: argparse.Namespace) -> int:
    try:
        workspace_identifier = args.workspace_flag or args.workspace
        if not workspace_identifier:
            print("Error: workspace slug or UUID is required", file=sys.stderr)
            return 1
        user_uuid = uuid.UUID(args.user_id) if args.user_id else None
        res = services.run_week(
            workspace_identifier=workspace_identifier,
            campaign_identifier=args.campaign,
            user_id=user_uuid,
            offline=bool(args.offline),
            window_days=int(args.window_days),
        )
        print(
            f"Run {res.get('id')} finished with status '{res.get('status')}': "
            f"cost {res.get('cost_usd', 0)} USD"
        )
        return 0 if res.get("status") == "completed" else 1
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


def _handle_run_stage(args: argparse.Namespace) -> int:
    try:
        res = services.run_stage(
            stage_or_record_id=args.id,
            stage=args.stage,
            offline=bool(args.offline),
        )
        if res.get("type") == "research_record":
            rec = res.get("record") or {}
            print(f"Re-run record {rec.get('id')}: status '{rec.get('status')}'")
        else:
            run = res.get("run") or {}
            count = res.get("re_run_items_count", 0)
            print(
                f"Re-run stage for run {run.get('id')}: "
                f"re-processed {count} item(s), status '{run.get('status')}'"
            )
        return 0
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


def _handle_run_now(args: argparse.Namespace) -> int:
    try:
        ws = services.resolve_workspace(workspace_identifier=args.workspace)
        ws_id = uuid.UUID(str(ws["workspace_id"]))
        actor_id = uuid.UUID(args.user_id) if args.user_id else ws.get("owner_user_id")
        if not actor_id:
            print("Error: No user or owner found for workspace", file=sys.stderr)
            return 1
        camp = services.resolve_campaign(workspace_id=ws_id, campaign_identifier=args.campaign)
        camp_id = uuid.UUID(str(camp["id"]))
        run_id = services.run_now(
            workspace_id=ws_id,
            campaign_id=camp_id,
            user_id=actor_id,
        )
        print(f"Queued run {run_id} for campaign '{camp.get('name')}'")
        if args.offline:
            res = services.execute_run_pipeline(
                workspace_id=ws_id,
                run_id=run_id,
                user_id=actor_id,
                offline=True,
            )
            print(f"Run {run_id} executed with status '{res.get('status')}'")
        return 0
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


def _handle_crontab(args: argparse.Namespace) -> int:
    try:
        ws_id = None
        if args.workspace:
            ws = services.resolve_workspace(workspace_identifier=args.workspace)
            ws_id = uuid.UUID(str(ws["workspace_id"]))
        crontab_content = services.generate_crontab(workspace_id=ws_id)
        sys.stdout.write(crontab_content)
        return 0
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


def _handle_webhooks_deliver(args: argparse.Namespace) -> int:
    try:
        batch_size = int(args.batch_size)
        results = services.deliver_webhooks(batch_size=batch_size)
        print(f"Processed {len(results)} webhook deliveries")
        return 0
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


def _handle_import_sitemap(args: argparse.Namespace) -> int:
    try:
        sitemap_url = str(args.url).strip()
        ws_id, owner_id = _resolve_workspace_and_owner(args.workspace)
        res = services.import_sitemap(
            workspace_id=ws_id,
            sitemap_url=sitemap_url,
            user_id=owner_id,
            fetch_titles=bool(args.fetch_titles),
        )
        print(
            f"Imported sitemap {sitemap_url}: {res['inserted']} inserted, "
            f"{res['updated']} updated (total: {res['total']})"
        )
        return 0
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


def _is_stateless_mode(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "stateless", False)) or os.getenv(
        "MACY_STATELESS", ""
    ).strip().lower() in {"1", "true", "yes"}


def _handle_import_draft(args: argparse.Namespace) -> int:
    try:
        if _is_stateless_mode(args):
            res = services.check_draft_stateless(
                file_path=str(args.file),
                format_slug=str(args.format),
                topic=args.topic,
                primary_keyword=args.primary_keyword,
            )
            if getattr(args, "json", False):
                print(json.dumps(res, indent=2, default=str))
            else:
                print(
                    f"Checked draft '{res['title']}' (stateless, verdict={res['verdict']}, "
                    f"findings={len(res['findings'])})"
                )
            return 0

        ws_id, owner_id = _resolve_workspace_and_owner(args.workspace)
        brief_uuid = uuid.UUID(str(args.brief_id)) if args.brief_id else None
        res = services.import_external_draft(
            workspace_id=ws_id,
            user_id=owner_id,
            file_path=str(args.file),
            brief_id=brief_uuid,
            format_slug=str(args.format),
            topic=args.topic,
            primary_keyword=args.primary_keyword,
            deterministic_only=bool(args.deterministic_only),
        )
        print(
            json.dumps(res, indent=2, default=str)
            if getattr(args, "json", False)
            else f"Imported draft {res['draft_id']} (rev {res['revision']}, "
            f"verdict={res['verdict']}, findings={len(res['findings'])})"
        )
        return 0
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


def _handle_audit(args: argparse.Namespace) -> int:
    cfg = CrawlConfig(plan=args.plan, max_pages=args.max_pages)
    if args.crawl:
        res = services.run_site_crawl(start_url=str(args.url), config=cfg)
        summary = (
            f"Crawl {res['start_url']}: {res['overall_score']} ({res['grade']}) across "
            f"{res['pages_crawled']} pages"
        )
    else:
        res = services.run_url_audit(url=str(args.url), crawl_config=cfg)
        summary = f"Audit {res['url']}: {res['overall_score']} ({res['grade']})"
    print(json.dumps(res, indent=2, default=str) if args.json else summary)
    return 0


def _handle_purge(args: argparse.Namespace) -> int:
    try:
        ref_now: datetime | None = None
        if args.now:
            raw_now = str(args.now).strip()
            if raw_now.endswith("Z"):
                raw_now = raw_now[:-1] + "+00:00"
            ref_now = datetime.fromisoformat(raw_now)
            if ref_now.tzinfo is None:
                ref_now = ref_now.replace(tzinfo=UTC)
        ws_id = uuid.UUID(str(args.workspace_id)) if args.workspace_id else None
        counts = services.purge_expired_data(now=ref_now, workspace_id=ws_id)
        print(
            f"Purged expired data: "
            f"{counts['llm_payloads_deleted']} llm_payloads deleted, "
            f"{counts['documents_text_purged']} documents text purged, "
            f"{counts['webhook_deliveries_deleted']} webhook_deliveries deleted, "
            f"{counts['events_deleted']} events deleted"
        )
        return 0
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


def _handle_anonymise_user(args: argparse.Namespace) -> int:
    try:
        res = services.anonymise_user(email=str(args.email))
        print(
            f"Anonymised user {res['user_id']} -> {res['anonymised_email']} "
            f"({res['memberships_removed']} membership(s) removed, "
            f"{res['events_scrubbed']} event(s) scrubbed)"
        )
        return 0
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


def _handle_test_sentry(args: argparse.Namespace) -> int:
    del args
    try:
        event_id = observability.send_test_sentry_event()
        print(f"Sent Sentry test event: {event_id}")
        return 0
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


def _handle_mcp(args: argparse.Namespace) -> int:
    try:
        use_stateless = _is_stateless_mode(args)
        guard = mcp_server._protect_stdio_stream() if args.transport == "stdio" else nullcontext()
        with guard:
            bundle = mcp_server.create_mcp_server(
                mock=bool(args.mock),
                allow_owner_fallback=bool(args.allow_owner_fallback),
                transport=args.transport,
                stateless=use_stateless,
            )
        if args.check:
            mode = "stateless" if bundle.stateless else ("mock" if bundle.mock else "db")
            stream = sys.stderr if args.transport == "stdio" else sys.stdout
            print(f"MCP check OK (mode={mode}, transport={args.transport})", file=stream)
            return 0
        if args.transport == "streamable-http":
            mcp_server.run_streamable_http_server(bundle, host=str(args.host), port=int(args.port))
            return 0
        mcp_server.run_stdio_server(bundle)
        return 0
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:  # noqa: PLR0915
    parser = argparse.ArgumentParser(prog="macy")
    subparsers = parser.add_subparsers(dest="subcommand")

    # plugins list
    plugins_parser = subparsers.add_parser("plugins")
    plugins_subparsers = plugins_parser.add_subparsers(dest="plugins_action")
    plugins_list_parser = plugins_subparsers.add_parser("list")
    plugins_list_parser.set_defaults(func=_handle_plugins_list)

    # capabilities list|enable|disable
    cap_parser = subparsers.add_parser("capabilities", help="Inspect and toggle capability plugins")
    cap_sub = cap_parser.add_subparsers(dest="capabilities_action")
    cap_list = cap_sub.add_parser("list")
    cap_list.add_argument("--workspace", default=None)
    cap_list.set_defaults(func=_handle_capabilities_list)
    for action_name, flag in (("enable", True), ("disable", False)):
        sub = cap_sub.add_parser(action_name)
        sub.add_argument("slug", help="Capability plugin slug")
        sub.add_argument("--workspace", required=True, help="Workspace slug or UUID")
        sub.add_argument("--user-id", default=None, help="Owner user UUID")
        sub.set_defaults(func=lambda a, en=flag: _handle_capabilities_toggle(a, enable=en))

    # integrations set
    integrations_parser = subparsers.add_parser("integrations")
    integrations_subparsers = integrations_parser.add_subparsers(dest="integrations_action")
    set_parser = integrations_subparsers.add_parser("set")
    set_parser.add_argument("--workspace-id", required=True)
    set_parser.add_argument("--port", required=True)
    set_parser.add_argument("--plugin", required=True)
    set_parser.add_argument("--settings", default=None)
    set_parser.add_argument("--secrets", default=None)
    set_parser.add_argument("--user-id", required=True)
    set_parser.set_defaults(func=_handle_integrations_set)

    preset_parser = integrations_subparsers.add_parser("preset")
    preset_subparsers = preset_parser.add_subparsers(dest="preset_action")
    preset_list_parser = preset_subparsers.add_parser("list")
    preset_list_parser.add_argument("--json", dest="json_output", action="store_true")
    preset_list_parser.set_defaults(func=_handle_integrations_preset_list)

    preset_apply_parser = preset_subparsers.add_parser("apply")
    preset_apply_parser.add_argument("preset_name")
    preset_apply_parser.add_argument("--workspace-id", required=True)
    preset_apply_parser.add_argument("--user-id", required=True)
    preset_apply_parser.add_argument("--json", dest="json_output", action="store_true")
    preset_apply_parser.set_defaults(func=_handle_integrations_preset_apply)

    # rotate-key
    rotate_parser = subparsers.add_parser("rotate-key")
    rotate_parser.add_argument("--current-keys", required=True)
    rotate_parser.add_argument("--new-key", required=True)
    rotate_parser.add_argument("--workspace-id", default=None)
    rotate_parser.set_defaults(func=_handle_rotate_key)

    # create-workspace
    create_ws_parser = subparsers.add_parser("create-workspace")
    create_ws_parser.add_argument("slug", help="Workspace slug (lowercase alphanumeric/hyphens)")
    create_ws_parser.add_argument("--name", required=True, help="Workspace display name")
    create_ws_parser.add_argument(
        "--pack",
        default=None,
        help="Industry pack slug (defaults to MACY_DEFAULT_PACK or 'general')",
    )
    create_ws_parser.add_argument(
        "--timezone",
        default="America/Sao_Paulo",
        help="IANA timezone for schedule evaluation (default: America/Sao_Paulo)",
    )
    create_ws_parser.set_defaults(func=_handle_create_workspace)

    # init (one-command guided Pocket setup)
    init_parser = subparsers.add_parser("init", help="Initialize first workspace, owner, and stack")
    init_parser.add_argument("--email", default=None)
    init_parser.add_argument("--password", default=None)
    init_parser.add_argument("--slug", default=None)
    init_parser.add_argument("--name", default=None)
    init_parser.add_argument("--pack", default=None)
    init_parser.add_argument("--timezone", default="America/Sao_Paulo")
    init_parser.add_argument("--capabilities", default="core-compliance,seo-audit")
    init_parser.add_argument("--preset", default="pocket_free")
    init_parser.add_argument("--site-url", default=None)
    init_parser.add_argument("--non-interactive", action="store_true")
    init_parser.add_argument("--json", dest="json_output", action="store_true")
    init_parser.set_defaults(func=_handle_init)

    # add-owner
    add_owner_parser = subparsers.add_parser("add-owner")
    add_owner_parser.add_argument("workspace", help="Workspace slug")
    add_owner_parser.add_argument("email", help="Owner email address")
    add_owner_parser.set_defaults(func=_handle_add_owner)

    # seed-brand
    seed_brand_parser = subparsers.add_parser("seed-brand")
    seed_brand_parser.add_argument("workspace", help="Workspace slug")
    seed_brand_parser.add_argument(
        "--fixture",
        default=None,
        help="Path or filename of brand profile YAML fixture (defaults to seed_example.yaml)",
    )
    seed_brand_parser.set_defaults(func=_handle_seed_brand)

    # seed-sources
    seed_sources_parser = subparsers.add_parser("seed-sources")
    seed_sources_parser.add_argument(
        "--pack",
        default=None,
        help="Industry pack slug to seed shared sources from (defaults to MACY_DEFAULT_PACK)",
    )
    seed_sources_parser.set_defaults(func=_handle_seed_sources)

    # run-week
    run_week_parser = subparsers.add_parser("run-week")
    run_week_parser.add_argument(
        "workspace",
        nargs="?",
        default=None,
        help="Workspace slug or UUID",
    )
    run_week_parser.add_argument(
        "--workspace",
        dest="workspace_flag",
        default=None,
        help="Workspace slug or UUID (used by generated crontab entries)",
    )
    run_week_parser.add_argument("--campaign", default=None, help="Campaign name or UUID")
    run_week_parser.add_argument("--user-id", default=None, help="Actor user UUID")
    run_week_parser.add_argument(
        "--offline",
        action="store_true",
        help="Run pipeline with fake adapters",
    )
    run_week_parser.add_argument(
        "--window-days",
        type=int,
        default=7,
        help="Research lookback window in days",
    )
    run_week_parser.set_defaults(func=_handle_run_week)

    # run-stage
    run_stage_parser = subparsers.add_parser("run-stage")
    run_stage_parser.add_argument("id", help="Run UUID or research record UUID to re-run")
    run_stage_parser.add_argument("--stage", default=None, help="Stage to re-run (e.g. research)")
    run_stage_parser.add_argument(
        "--offline",
        action="store_true",
        help="Run stage with fake adapters",
    )
    run_stage_parser.set_defaults(func=_handle_run_stage)

    # run-now
    run_now_parser = subparsers.add_parser("run-now")
    run_now_parser.add_argument("workspace", help="Workspace slug or UUID")
    run_now_parser.add_argument("--campaign", default=None, help="Campaign name or UUID")
    run_now_parser.add_argument("--user-id", default=None, help="Actor user UUID")
    run_now_parser.add_argument(
        "--offline",
        action="store_true",
        help="Execute queued run immediately with fake adapters",
    )
    run_now_parser.set_defaults(func=_handle_run_now)

    # crontab
    crontab_parser = subparsers.add_parser("crontab")
    crontab_parser.add_argument("--workspace", default=None, help="Workspace slug or UUID")
    crontab_parser.set_defaults(func=_handle_crontab)

    # verify-sources
    verify_sources_parser = subparsers.add_parser("verify-sources")
    verify_sources_parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Verification lookback window in days (default: 30)",
    )
    verify_sources_parser.add_argument(
        "--pack",
        "--industry-pack",
        dest="pack",
        default=None,
        help="Industry pack slug to verify (defaults to MACY_DEFAULT_PACK or 'general')",
    )
    verify_sources_parser.add_argument(
        "--offline",
        action="store_true",
        help="Run verification with fake adapters",
    )
    verify_sources_parser.set_defaults(func=_handle_verify_sources)

    # webhooks-deliver
    webhooks_deliver_parser = subparsers.add_parser("webhooks-deliver")
    webhooks_deliver_parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Maximum deliveries to process in one batch (default: 10)",
    )
    webhooks_deliver_parser.set_defaults(func=_handle_webhooks_deliver)

    # import-sitemap
    import_sitemap_parser = subparsers.add_parser("import-sitemap")
    import_sitemap_parser.add_argument("url", help="Sitemap URL or local file path to import")
    import_sitemap_parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace slug or UUID (default: first workspace)",
    )
    import_sitemap_parser.add_argument(
        "--fetch-titles",
        action="store_true",
        default=False,
        help="Fetch article web pages to extract exact HTML title",
    )
    import_sitemap_parser.set_defaults(func=_handle_import_sitemap)

    # import-draft
    import_draft_parser = subparsers.add_parser("import-draft")
    import_draft_parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace slug or UUID (default: first workspace)",
    )
    import_draft_parser.add_argument(
        "--file",
        required=True,
        help="Path to .md or .pdf draft file",
    )
    import_draft_parser.add_argument(
        "--format",
        default="blog",
        help="Format pack slug (e.g. blog-short, blog, blog-long)",
    )
    import_draft_parser.add_argument(
        "--brief-id",
        default=None,
        help="Optional existing brief UUID for revision 1..2",
    )
    import_draft_parser.add_argument(
        "--topic",
        default=None,
        help="Optional topic override",
    )
    import_draft_parser.add_argument(
        "--primary-keyword",
        default=None,
        help="Optional primary keyword override",
    )
    import_draft_parser.add_argument(
        "--deterministic-only",
        action="store_true",
        default=False,
        help="Run deterministic compliance checks only (skip LLM rubric)",
    )
    import_draft_parser.add_argument(
        "--stateless",
        action="store_true",
        default=False,
        help="Run deterministic compliance + JSON-LD without database connection (Path B)",
    )
    import_draft_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit full structured JSON output",
    )
    import_draft_parser.set_defaults(func=_handle_import_draft)

    # audit
    audit_parser = subparsers.add_parser("audit", help="Run SEO + GEO URL audit or site crawl")
    audit_parser.add_argument("url", help="Public HTTP(S) URL to audit")
    audit_parser.add_argument(
        "--crawl",
        action="store_true",
        default=False,
        help="Run bounded BFS site crawl instead of single-URL audit",
    )
    audit_parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Maximum pages for site crawl (free default 20, paid 50, cap 200)",
    )
    audit_parser.add_argument(
        "--plan",
        choices=["free", "paid"],
        default="free",
        help="Workspace plan tier governing crawl limits (default: free)",
    )
    audit_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit full structured JSON report",
    )
    audit_parser.add_argument(
        "--stateless",
        action="store_true",
        default=False,
        help="Run zero-database stateless audit/crawl (Path B)",
    )
    audit_parser.set_defaults(func=_handle_audit)

    # purge
    purge_parser = subparsers.add_parser("purge")
    purge_parser.add_argument(
        "--now",
        default=None,
        help="Reference ISO-8601 UTC timestamp for retention cutoffs (default: current UTC time)",
    )
    purge_parser.add_argument(
        "--workspace-id",
        default=None,
        help="Optional workspace UUID filter",
    )
    purge_parser.set_defaults(func=_handle_purge)

    # anonymise-user
    anonymise_parser = subparsers.add_parser("anonymise-user")
    anonymise_parser.add_argument("email", help="Email address of the user to anonymise")
    anonymise_parser.set_defaults(func=_handle_anonymise_user)

    # test-sentry
    test_sentry_parser = subparsers.add_parser("test-sentry")
    test_sentry_parser.set_defaults(func=_handle_test_sentry)

    # mcp
    mcp_parser = subparsers.add_parser("mcp", help="Run the Macy Model Context Protocol server")
    mcp_parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP transport mode (default: stdio)",
    )
    mcp_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="HTTP bind host for streamable-http (default: 127.0.0.1)",
    )
    mcp_parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="HTTP bind port for streamable-http (default: 8765)",
    )
    mcp_parser.add_argument(
        "--mock",
        action="store_true",
        help="Use in-memory demo store (forbidden in staging/production)",
    )
    mcp_parser.add_argument(
        "--allow-owner-fallback",
        action="store_true",
        help="Allow non-production fallback to workspace owner when stdio identity is omitted",
    )
    mcp_parser.add_argument(
        "--stateless",
        action="store_true",
        default=False,
        help="Run zero-database stateless stdio MCP server for audit and compliance (Path B)",
    )
    mcp_parser.add_argument(
        "--check",
        action="store_true",
        help="Validate startup config and store/DB connectivity without starting the server",
    )
    mcp_parser.set_defaults(func=_handle_mcp)

    return parser


def main(args: list[str] | None = None) -> int:
    """CLI main entry point."""
    parser = _build_parser()

    if args is None and any("pytest" in arg for arg in sys.argv):
        parser.print_help()
        return 0

    cmd_args = sys.argv[1:] if args is None else args
    if not cmd_args:
        parser.print_help()
        return 0

    parsed_args = parser.parse_args(cmd_args)
    handler = getattr(parsed_args, "func", None)
    if handler is not None:
        observability.ensure_sentry_initialized()
        return handler(parsed_args)  # type: ignore[no-any-return]

    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
