# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Command-line entrypoint for GitHub Actions and local dry-run inspection."""

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
from .models import GuardConfig, ParsedIssue, RawIssue
from .normalize import compact_name
from .parser import parse_issue


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


def build_duplicate_links(raw_issues: list[RawIssue], client: GitHubClient | None) -> dict[int, int]:
    """Resolve existing canonical references from duplicate-marked issues without spamming API."""
    links: dict[int, int] = {}
    if client is None:
        return links
    for raw in raw_issues:
        is_dup = raw.state_reason == "duplicate" or any(l.lower() == "duplicado" for l in raw.labels)
        if is_dup and raw.comments_count > 0:
            try:
                comments = client.list_comments(raw.number)
                canon = extract_canonical_reference(comments)
                if canon and canon != raw.number:
                    links[raw.number] = canon
            except GitHubApiError:
                continue
    return links


def _apply_cli_overrides(cfg: GuardConfig, args: argparse.Namespace) -> GuardConfig:
    dry_run = cfg.dry_run
    auto_close = cfg.auto_close_enabled
    if args.dry_run is not None:
        dry_run = args.dry_run
    if args.auto_close in ("true", "1", "yes"):
        auto_close = True
    elif args.auto_close in ("false", "0", "no"):
        auto_close = False
    return replace(cfg, dry_run=dry_run, auto_close_enabled=auto_close)


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


def _write_summary(summary_path: str | None, results: list[dict]) -> None:
    if not summary_path:
        return
    lines = [
        "### 🛡️ Nox Duplicate Guard — Relatório de Execução",
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


def run_cli(argv: list[str] | None = None) -> int:
    """Execute single-issue or reconciliation duplicate guard workflow."""
    ap = argparse.ArgumentParser(description="Nox Duplicate Guard CLI")
    ap.add_argument("--mode", choices=["single", "reconcile"], default="single")
    ap.add_argument("--issue-number", type=int, default=None)
    ap.add_argument("--lookback-days", type=int, default=None)
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
    client: GitHubClient | None = None

    if args.fixtures_file:
        raw_issues = _load_issues_from_fixtures(Path(args.fixtures_file))
        # Ensure dry_run when running against offline fixtures
        config = replace(config, dry_run=True)
    else:
        client = GitHubClient(args.repo, token=token, dry_run=config.dry_run)
        try:
            raw_dicts = client.list_issues(state="all")
        except (RateLimitError, PermissionDeniedError, GitHubApiError) as exc:
            print(json.dumps({"status": "conservative_abort", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            # Fail conservatively without breaking unrelated workflow expectations if transient/rate-limited
            return 0 if isinstance(exc, RateLimitError) else 1
        raw_issues = [RawIssue.from_github_dict(d) for d in raw_dicts]

    issues_by_num = {r.number: r for r in raw_issues}
    parsed_list = parse_corpus(raw_issues)
    parsed_by_num = {p.raw.number: p for p in parsed_list}
    dup_links = build_duplicate_links(raw_issues, client)
    matcher = DuplicateMatcher(parsed_list, config, duplicate_links=dup_links)

    target_numbers: list[int] = []
    if args.mode == "single":
        issue_num = _resolve_issue_number_from_env(args.issue_number)
        if issue_num is None:
            print(json.dumps({"status": "skipped_no_issue_or_pr", "results": []}, ensure_ascii=False))
            return 0
        if issue_num not in parsed_by_num and client is not None:
            try:
                single_dict = client.get_issue(issue_num)
                if "pull_request" not in single_dict:
                    raw_single = RawIssue.from_github_dict(single_dict)
                    issues_by_num[issue_num] = raw_single
                    parsed_by_num[issue_num] = parse_issue(raw_single, _collect_known_extensions(raw_issues))
            except GitHubApiError as exc:
                print(json.dumps({"status": "conservative_abort", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
                return 1
        if issue_num in parsed_by_num:
            target_numbers.append(issue_num)
    else:
        lookback = args.lookback_days if args.lookback_days is not None else config.reconcile_lookback_days
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max(1, lookback))
        for p in sorted(parsed_list, key=lambda x: (_parse_iso(x.raw.created_at), x.raw.number)):
            if p.is_open and _parse_iso(p.raw.created_at) >= cutoff:
                target_numbers.append(p.raw.number)

    report_rows: list[dict] = []
    executor = GuardActionExecutor(client, config, issues_by_num) if client is not None else None

    for num in target_numbers:
        target_parsed = parsed_by_num[num]
        decision = matcher.evaluate(target_parsed)
        if executor is not None:
            action_res = executor.apply(decision)
        else:
            action_res = {
                "issue_number": num,
                "decision": decision.classification,
                "canonical_issue": decision.canonical_issue,
                "action_taken": f"offline_{decision.classification}",
                "dry_run": True,
                "closed": False,
                "skipped_reason": decision.blocked_reason,
            }

        row = {
            "issue_number": num,
            "category": decision.category,
            "classification": decision.classification,
            "canonical_issue": decision.canonical_issue,
            "should_auto_close": decision.should_auto_close,
            "reasons": list(decision.reasons),
            "candidates": [asdict(c) for c in decision.candidates],
            "action_taken": action_res.action_taken if hasattr(action_res, "action_taken") else action_res["action_taken"],
            "dry_run": config.dry_run,
            "skipped_reason": action_res.skipped_reason if hasattr(action_res, "skipped_reason") else action_res.get("skipped_reason"),
            "partial_errors": list(action_res.partial_errors) if hasattr(action_res, "partial_errors") else [],
        }
        report_rows.append(row)

    _write_summary(args.summary_file, report_rows)
    print(
        json.dumps(
            {
                "status": "ok",
                "mode": args.mode,
                "repo": args.repo,
                "dry_run": config.dry_run,
                "evaluated_count": len(report_rows),
                "api_calls_made": client.api_calls_made if client else 0,
                "results": report_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
