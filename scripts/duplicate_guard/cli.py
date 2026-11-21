# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Command-line entrypoint for event-driven, incremental, and historical backfill runs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .actions import GuardActionExecutor
from .config import DEFAULT_CONFIG_PATH, load_config
from .github_client import GitHubApiError, GitHubClient, PermissionDeniedError, RateLimitError
from .matcher import DuplicateMatcher, extract_canonical_reference
from .models import RULE_ENGINE_VERSION, GuardConfig, ParsedIssue, RawIssue
from .parser import parse_issue

TERMINAL_CACHE_ACTIONS = frozenset({
    "noop",
    "noop_idempotent",
    "noop_already_updated",
    "commented",
    "auto_closed",
    "cleared",
    "skipped_manual_override",
})


def _parse_iso(ts: str) -> datetime:
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def _load_issues_from_fixtures(path: Path) -> list[RawIssue]:
    raw_data = json.loads(path.read_text(encoding="utf-8"))
    items = raw_data.get("issues", raw_data) if isinstance(raw_data, dict) else raw_data
    return [RawIssue.from_github_dict(item) for item in items if isinstance(item, dict) and "pull_request" not in item]


def _collect_known_extensions(raw_issues: list[RawIssue]) -> frozenset[str]:
    known: set[str] = set()
    for raw in raw_issues:
        parsed = parse_issue(raw)
        if parsed.compact_name and len(parsed.compact_name) >= 4:
            known.add(parsed.compact_name)
        if parsed.related_extension_compact and len(parsed.related_extension_compact) >= 4:
            known.add(parsed.related_extension_compact)
    return frozenset(known)


def parse_corpus(raw_issues: list[RawIssue]) -> list[ParsedIssue]:
    """Parse all issues in two passes so enhancement scope inference sees known extensions."""
    known = _collect_known_extensions(raw_issues)
    return [parse_issue(raw, known_extensions=known) for raw in raw_issues]


def build_duplicate_links(
    raw_issues: list[RawIssue],
    client: object | None,
    cache_state: dict | None = None,
) -> dict[int, int]:
    """Resolve canonical references from duplicate-marked issues using incremental cache."""
    links: dict[int, int] = {}
    if client is None:
        return links
    dup_cache = cache_state.setdefault("dup_links", {}) if isinstance(cache_state, dict) else None
    for raw in raw_issues:
        is_dup = raw.state_reason == "duplicate" or any(l.lower() == "duplicado" for l in raw.labels)
        if is_dup and raw.comments_count > 0:
            key = str(raw.number)
            if dup_cache is not None and isinstance(dup_cache.get(key), dict):
                cached = dup_cache[key]
                if cached.get("updated_at") == raw.updated_at:
                    canon_cached = cached.get("canonical")
                    if isinstance(canon_cached, int) and canon_cached != raw.number:
                        links[raw.number] = canon_cached
                    continue
            try:
                comments = client.list_comments(raw.number)  # type: ignore[attr-defined]
                canon = extract_canonical_reference(comments)
                if dup_cache is not None:
                    dup_cache[key] = {"updated_at": raw.updated_at, "canonical": canon}
                if canon and canon != raw.number:
                    links[raw.number] = canon
            except GitHubApiError:
                continue
    return links


def _apply_cli_overrides(cfg: GuardConfig, args: argparse.Namespace) -> GuardConfig:
    dry_run = cfg.dry_run
    auto_close = cfg.auto_close_enabled
    batch_size = cfg.batch_size
    if args.dry_run is not None:
        dry_run = args.dry_run
    if args.auto_close in ("true", "1", "yes"):
        auto_close = True
    elif args.auto_close in ("false", "0", "no"):
        auto_close = False
    if args.batch_size is not None and args.batch_size > 0:
        batch_size = args.batch_size
    return replace(cfg, dry_run=dry_run, auto_close_enabled=auto_close, batch_size=batch_size)


def _resolve_issue_number_from_env(explicit: int | None) -> int | None:
    if explicit:
        return explicit
    event_path = os.getenv("GITHUB_EVENT_PATH")
    if event_path and Path(event_path).exists():
        try:
            payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
            if isinstance(payload.get("issue"), dict) and payload["issue"].get("number"):
                if "pull_request" in payload["issue"]:
                    return None
                return int(payload["issue"]["number"])
            if isinstance(payload.get("inputs"), dict) and payload["inputs"].get("issue_number"):
                return int(payload["inputs"]["issue_number"])
        except Exception:
            return None
    return None


def _load_cache(cache_path: Path) -> dict:
    empty = {
        "rule_version": RULE_ENGINE_VERSION,
        "last_full_backfill_at": None,
        "evaluated": {},
        "dup_links": {},
    }
    if not cache_path.exists():
        return empty
    try:
        loaded = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return empty
        if loaded.get("rule_version") != RULE_ENGINE_VERSION:
            return empty
        evaluated = loaded.get("evaluated")
        dup_links = loaded.get("dup_links")
        return {
            "rule_version": RULE_ENGINE_VERSION,
            "last_full_backfill_at": loaded.get("last_full_backfill_at"),
            "evaluated": evaluated if isinstance(evaluated, dict) else {},
            "dup_links": dup_links if isinstance(dup_links, dict) else {},
        }
    except Exception:
        return empty


def _save_cache(cache_path: Path, state: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(cache_path)


def _is_cache_hit(
    entry: dict | None,
    target: ParsedIssue,
    decision_cls: str,
    canonical_issue: int | None,
    config: GuardConfig,
    issues_by_num: dict[int, RawIssue],
) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("updated_at") != target.raw.updated_at:
        return False
    if entry.get("fingerprint") != target.content_fingerprint:
        return False
    if entry.get("auto_close_enabled") != config.auto_close_enabled:
        return False
    if entry.get("decision") != decision_cls:
        return False
    if entry.get("canonical_issue") != canonical_issue:
        return False
    if entry.get("action_taken") not in TERMINAL_CACHE_ACTIONS:
        return False
    if canonical_issue is not None:
        canon_raw = issues_by_num.get(canonical_issue)
        canon_state = canon_raw.state if canon_raw else None
        if canon_state != entry.get("canonical_state"):
            return False
    return True


def _write_summary(summary_path: str | None, meta: dict, results: list[dict]) -> None:
    if not summary_path:
        return
    lines = [
        "### 🛡️ Nox Duplicate Guard — Relatório de Execução",
        "",
        (
            f"- **Modo efetivo:** `{meta['effective_mode']}` | "
            f"**Dry-Run:** `{meta['dry_run']}` | "
            f"**Auto-Close:** `{meta['auto_close_enabled']}`"
        ),
        (
            f"- **Issues abertas analisadas:** `{meta['evaluated_count']}` / `{meta['total_open_issues']}` "
            f"(cache inalterado: `{meta['skipped_cached_count']}`)"
        ),
        (
            f"- **Duplicatas comprovadas:** `{meta['confirmed_duplicates_count']}` | "
            f"**Possíveis duplicatas:** `{meta['possible_duplicates_count']}` | "
            f"**Fechadas / Simuladas:** `{meta['would_or_did_close']}` | "
            f"**Chamadas API:** `{meta['api_calls_made']}`"
        ),
        "",
        "| Issue | Categoria | Classificação | Original | Ação | Dry-Run |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for row in results:
        canon = f"#{row['canonical_issue']}" if row["canonical_issue"] else "—"
        lines.append(
            f"| #{row['issue_number']} | `{row['category']}` | `{row['classification']}` | {canon} | `{row['action_taken']}` | `{row['dry_run']}` |"
        )
    Path(summary_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_cli(argv: list[str] | None = None, *, client_override: object | None = None) -> int:
    """Execute single-issue, incremental reconcile, or historical backfill workflow."""
    ap = argparse.ArgumentParser(description="Nox Duplicate Guard CLI")
    ap.add_argument("--mode", choices=["single", "reconcile", "backfill"], default="single")
    ap.add_argument("--issue-number", type=int, default=None)
    ap.add_argument("--lookback-days", type=int, default=None)
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument(
        "--cache-file",
        default=os.getenv("NOX_DUPLICATE_GUARD_CACHE_FILE", ".cache/nox-duplicate-guard/state.json"),
    )
    ap.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", "ProjectNox-org/project-nox-requests"))
    ap.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    ap.add_argument("--fixtures-file", default=None, help="Run completely offline against a JSON issue snapshot")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true", default=None)
    ap.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    ap.add_argument("--auto-close", choices=["config", "true", "false"], default="config")
    ap.add_argument("--summary-file", default=os.getenv("GITHUB_STEP_SUMMARY"))
    args = ap.parse_args(argv)

    config = _apply_cli_overrides(load_config(args.config), args)
    if not config.enabled:
        print(json.dumps({"status": "disabled", "results": []}, ensure_ascii=False))
        return 0

    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or ""
    client: object | None = client_override
    cache_path = Path(args.cache_file)
    cache_state = _load_cache(cache_path)

    if args.fixtures_file:
        raw_issues = _load_issues_from_fixtures(Path(args.fixtures_file))
        config = replace(config, dry_run=True)
        client = None
    else:
        if client is None:
            client = GitHubClient(args.repo, token=token, dry_run=config.dry_run)
        try:
            raw_dicts = client.list_issues(state="all")  # type: ignore[attr-defined]
        except (RateLimitError, PermissionDeniedError, GitHubApiError) as exc:
            print(json.dumps({"status": "conservative_abort", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 0 if isinstance(exc, RateLimitError) else 1
        raw_issues = [RawIssue.from_github_dict(d) for d in raw_dicts]

    issues_by_num = {r.number: r for r in raw_issues}
    parsed_list = parse_corpus(raw_issues)
    parsed_by_num = {p.raw.number: p for p in parsed_list}
    dup_links = build_duplicate_links(raw_issues, client, cache_state=cache_state)
    matcher = DuplicateMatcher(parsed_list, config, duplicate_links=dup_links)

    effective_mode = args.mode
    if effective_mode == "reconcile" and not cache_state.get("last_full_backfill_at"):
        # First incremental run (or after rule engine upgrade) automatically performs historical backfill.
        effective_mode = "backfill"

    target_numbers: list[int] = []
    if effective_mode == "single":
        issue_num = _resolve_issue_number_from_env(args.issue_number)
        if issue_num is None:
            print(json.dumps({"status": "skipped_no_issue_or_pr", "results": []}, ensure_ascii=False))
            return 0
        if issue_num not in parsed_by_num and client is not None:
            try:
                single_dict = client.get_issue(issue_num)  # type: ignore[attr-defined]
                if "pull_request" not in single_dict:
                    raw_single = RawIssue.from_github_dict(single_dict)
                    issues_by_num[issue_num] = raw_single
                    parsed_by_num[issue_num] = parse_issue(raw_single, _collect_known_extensions(raw_issues))
            except GitHubApiError as exc:
                print(json.dumps({"status": "conservative_abort", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
                return 1
        if issue_num in parsed_by_num:
            target_numbers.append(issue_num)
    elif effective_mode == "backfill":
        for p in sorted(parsed_list, key=lambda x: (_parse_iso(x.raw.created_at), x.raw.number)):
            if p.is_open:
                target_numbers.append(p.raw.number)
    else:
        lookback = args.lookback_days if args.lookback_days is not None else config.reconcile_lookback_days
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max(1, lookback))
        for p in sorted(parsed_list, key=lambda x: (_parse_iso(x.raw.created_at), x.raw.number)):
            if p.is_open and (_parse_iso(p.raw.created_at) >= cutoff or _parse_iso(p.raw.updated_at) >= cutoff):
                target_numbers.append(p.raw.number)

    report_rows: list[dict] = []
    executor = GuardActionExecutor(client, config, issues_by_num) if client is not None else None  # type: ignore[arg-type]

    can_persist_cache = (not config.dry_run) and (not args.fixtures_file) and (client is not None)
    processed_api_batch = 0
    skipped_cached_count = 0
    batch_truncated = False
    rate_limited_stop = False

    for num in target_numbers:
        target_parsed = parsed_by_num[num]
        decision = matcher.evaluate(target_parsed)

        if can_persist_cache and effective_mode in ("reconcile", "backfill"):
            cached_entry = cache_state["evaluated"].get(str(num))
            if _is_cache_hit(
                cached_entry,
                target_parsed,
                decision.classification,
                decision.canonical_issue,
                config,
                issues_by_num,
            ):
                skipped_cached_count += 1
                report_rows.append({
                    "issue_number": num,
                    "category": decision.category,
                    "classification": decision.classification,
                    "canonical_issue": decision.canonical_issue,
                    "should_auto_close": decision.should_auto_close,
                    "reasons": list(decision.reasons),
                    "candidates": [asdict(c) for c in decision.candidates],
                    "action_taken": "skipped_unchanged_cache",
                    "dry_run": False,
                    "closed": False,
                    "skipped_reason": "unchanged_since_last_evaluation",
                    "partial_errors": [],
                })
                continue

        if effective_mode in ("reconcile", "backfill") and processed_api_batch >= config.batch_size:
            batch_truncated = True
            break

        if executor is not None:
            action_res = executor.apply(decision)
            processed_api_batch += 1
            action_taken = action_res.action_taken
            closed_flag = action_res.closed
            skipped_reason = action_res.skipped_reason
            partial_errors = list(action_res.partial_errors)
        else:
            processed_api_batch += 1
            action_taken = f"offline_{decision.classification}"
            closed_flag = False
            skipped_reason = decision.blocked_reason
            partial_errors = []

        if can_persist_cache and action_taken in TERMINAL_CACHE_ACTIONS:
            canon_state = issues_by_num[decision.canonical_issue].state if decision.canonical_issue in issues_by_num else None
            cache_state["evaluated"][str(num)] = {
                "updated_at": target_parsed.raw.updated_at,
                "fingerprint": target_parsed.content_fingerprint,
                "auto_close_enabled": config.auto_close_enabled,
                "decision": decision.classification,
                "canonical_issue": decision.canonical_issue,
                "canonical_state": canon_state,
                "action_taken": action_taken,
            }
            _save_cache(cache_path, cache_state)

        report_rows.append({
            "issue_number": num,
            "category": decision.category,
            "classification": decision.classification,
            "canonical_issue": decision.canonical_issue,
            "should_auto_close": decision.should_auto_close,
            "reasons": list(decision.reasons),
            "candidates": [asdict(c) for c in decision.candidates],
            "action_taken": action_taken,
            "dry_run": config.dry_run,
            "closed": closed_flag,
            "skipped_reason": skipped_reason,
            "partial_errors": partial_errors,
        })

        if skipped_reason and "rate limit" in skipped_reason.lower():
            rate_limited_stop = True
            break

    if can_persist_cache and effective_mode == "backfill" and not batch_truncated and not rate_limited_stop:
        cache_state["last_full_backfill_at"] = datetime.now(tz=timezone.utc).isoformat()
        _save_cache(cache_path, cache_state)

    total_open_issues = sum(1 for p in parsed_list if p.is_open)
    confirmed_count = sum(1 for r in report_rows if r["classification"] == "confirmed_duplicate")
    possible_count = sum(1 for r in report_rows if r["classification"] == "possible_duplicate")
    would_or_did_close = sum(
        1 for r in report_rows
        if r["action_taken"] in ("auto_closed", "dry_run_auto_closed")
    )
    kept_open_ambiguous = sum(
        1 for r in report_rows
        if r["classification"] == "possible_duplicate"
    )
    api_calls = getattr(client, "api_calls_made", 0) if client is not None else 0

    meta = {
        "status": "ok",
        "mode": args.mode,
        "effective_mode": effective_mode,
        "repo": args.repo,
        "dry_run": config.dry_run,
        "auto_close_enabled": config.auto_close_enabled,
        "rule_version": RULE_ENGINE_VERSION,
        "total_open_issues": total_open_issues,
        "evaluated_count": len(report_rows),
        "processed_via_api_count": processed_api_batch,
        "skipped_cached_count": skipped_cached_count,
        "confirmed_duplicates_count": confirmed_count,
        "possible_duplicates_count": possible_count,
        "would_or_did_close": would_or_did_close,
        "kept_open_ambiguous": kept_open_ambiguous,
        "batch_truncated": batch_truncated,
        "rate_limited_stop": rate_limited_stop,
        "api_calls_made": api_calls,
    }

    _write_summary(args.summary_file, meta, report_rows)
    print(json.dumps({**meta, "results": report_rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
