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
from .models import (
    LABEL_DUPLICATE,
    RULE_ENGINE_VERSION,
    ExtensionCatalog,
    GuardConfig,
    ParsedIssue,
    RawIssue,
)
from .parser import build_extension_catalog, parse_issue

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


def load_extension_catalog(
    client: object | None,
    cache_state: dict | None = None,
    *,
    ttl_seconds: int = 21600,
) -> ExtensionCatalog:
    """Load official manga/anime extension catalogs with 6-hour incremental cache."""
    if client is None or not hasattr(client, "list_catalog_extensions"):
        return ExtensionCatalog()
    now_ts = datetime.now(tz=timezone.utc)
    if isinstance(cache_state, dict):
        cached = cache_state.get("catalog")
        if isinstance(cached, dict) and isinstance(cached.get("payload"), dict):
            fetched_at = _parse_iso(str(cached.get("fetched_at") or ""))
            if (now_ts - fetched_at).total_seconds() < ttl_seconds:
                return build_extension_catalog(cached["payload"])
    try:
        payload = client.list_catalog_extensions()  # type: ignore[attr-defined]
    except RateLimitError:
        if isinstance(cache_state, dict) and isinstance(cache_state.get("catalog"), dict):
            prev = cache_state["catalog"].get("payload")
            if isinstance(prev, dict):
                return build_extension_catalog(prev)
        raise
    except GitHubApiError:
        return ExtensionCatalog()
    if isinstance(cache_state, dict) and isinstance(payload, dict):
        cache_state["catalog"] = {"fetched_at": now_ts.isoformat(), "payload": payload}
    return build_extension_catalog(payload)


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
            except RateLimitError:
                raise
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
        "catalog": {},
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
        catalog = loaded.get("catalog")
        return {
            "rule_version": RULE_ENGINE_VERSION,
            "last_full_backfill_at": loaded.get("last_full_backfill_at"),
            "evaluated": evaluated if isinstance(evaluated, dict) else {},
            "dup_links": dup_links if isinstance(dup_links, dict) else {},
            "catalog": catalog if isinstance(catalog, dict) else {},
        }
    except Exception:
        return empty


def _save_cache(cache_path: Path, state: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(cache_path)


def _prune_cache(state: dict, open_numbers: set[int], all_numbers: set[int]) -> None:
    evaluated = state.get("evaluated")
    if isinstance(evaluated, dict):
        state["evaluated"] = {
            k: v for k, v in evaluated.items()
            if str(k).isdigit() and int(k) in open_numbers
        }
    dup_links = state.get("dup_links")
    if isinstance(dup_links, dict):
        state["dup_links"] = {
            k: v for k, v in dup_links.items()
            if str(k).isdigit() and int(k) in all_numbers
        }


def _has_valid_cache_entry(entry: dict | None, target: ParsedIssue, config: GuardConfig) -> bool:
    if not isinstance(entry, dict):
        return False
    return (
        entry.get("updated_at") == target.raw.updated_at
        and entry.get("fingerprint") == target.content_fingerprint
        and entry.get("auto_close_enabled") == config.auto_close_enabled
        and entry.get("action_taken") in TERMINAL_CACHE_ACTIONS
    )


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


def _is_rate_limit_failure(skipped_reason: object, partial_errors: list[str] | tuple[str, ...]) -> bool:
    if skipped_reason and "rate limit" in str(skipped_reason).lower():
        return True
    return any("rate limit" in str(err).lower() for err in (partial_errors or ()))


def _abort_api_error(args: argparse.Namespace, exc: Exception, summary_path: str | None) -> int:
    is_rate_limit = isinstance(exc, RateLimitError)
    status = "incomplete_rate_limited" if is_rate_limit else "fatal_error"
    recoverable = is_rate_limit
    rc = 3 if is_rate_limit else 1
    if summary_path:
        title = "Execução interrompida por limite de taxa (Rate Limit)" if is_rate_limit else "Falha fatal de API"
        Path(summary_path).write_text(
            "\n".join([
                "### 🛡️ Nox Duplicate Guard — Relatório de Execução",
                "",
                "> [!WARNING]",
                f"> **{title}:** `{status}` — {exc}",
                "",
            ]),
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "status": status,
                "completed": False,
                "recoverable": recoverable,
                "mode": args.mode,
                "repo": args.repo,
                "error": str(exc),
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    return rc


def _write_summary(summary_path: str | None, meta: dict, results: list[dict]) -> None:
    if not summary_path:
        return
    lines = [
        "### 🛡️ Nox Duplicate Guard — Relatório de Execução",
        "",
        (
            f"- **Status:** `{meta['status']}` | "
            f"**Modo efetivo:** `{meta['effective_mode']}` | "
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
            f"**Erros parciais:** `{meta['error_count']}` | "
            f"**Chamadas API:** `{meta['api_calls_made']}`"
        ),
        "",
    ]
    if meta.get("rate_limited_stop"):
        lines.extend([
            "> [!WARNING]",
            "> **Execução interrompida por limite de taxa (Rate Limit):** Progresso parcial salvo no cache (`status: incomplete_rate_limited`). O próximo ciclo retomará automaticamente as Issues pendentes.",
            "",
        ])
    elif meta.get("batch_truncated"):
        lines.extend([
            "> [!NOTE]",
            "> **Lote truncado (`incomplete_batch`):** Há Issues pendentes; o próximo ciclo retomará do ponto salvo no cache.",
            "",
        ])
    if meta.get("error_count", 0) > 0:
        lines.extend([
            "> [!WARNING]",
            f"> **Alerta de Falha Parcial / API:** `{meta['error_count']}` Issue(s) tiveram erro durante o processamento. Consulte o JSON nos logs para detalhes.",
            "",
        ])
    lines.extend([
        "| Issue | Categoria | Classificação | Original | Ação | Dry-Run |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ])
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
        can_persist_cache = False
    else:
        if client is None:
            client = GitHubClient(args.repo, token=token, dry_run=config.dry_run)
        can_persist_cache = (not config.dry_run) and (client is not None)
        try:
            raw_dicts = client.list_issues(state="all")  # type: ignore[attr-defined]
        except (RateLimitError, PermissionDeniedError, GitHubApiError) as exc:
            return _abort_api_error(args, exc, args.summary_file)
        raw_issues = [RawIssue.from_github_dict(d) for d in raw_dicts]

    issues_by_num = {r.number: r for r in raw_issues}
    parsed_list = parse_corpus(raw_issues)
    parsed_by_num = {p.raw.number: p for p in parsed_list}
    try:
        dup_links = build_duplicate_links(raw_issues, client, cache_state=cache_state)
        catalog = load_extension_catalog(client, cache_state=cache_state)
    except (RateLimitError, PermissionDeniedError, GitHubApiError) as exc:
        if can_persist_cache:
            _save_cache(cache_path, cache_state)
        return _abort_api_error(args, exc, args.summary_file)
    matcher = DuplicateMatcher(parsed_list, config, duplicate_links=dup_links, catalog=catalog)

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
            except (RateLimitError, PermissionDeniedError, GitHubApiError) as exc:
                return _abort_api_error(args, exc, args.summary_file)
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
            if not p.is_open:
                continue
            cached_entry = cache_state["evaluated"].get(str(p.raw.number))
            if (
                _parse_iso(p.raw.created_at) >= cutoff
                or _parse_iso(p.raw.updated_at) >= cutoff
                or not _has_valid_cache_entry(cached_entry, p, config)
            ):
                target_numbers.append(p.raw.number)

    report_rows: list[dict] = []
    executor = (
        GuardActionExecutor(client, config, issues_by_num, repo=args.repo, matcher=matcher)  # type: ignore[arg-type]
        if client is not None
        else None
    )

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
            eff_cls = action_res.decision
            eff_canon = action_res.canonical_issue
            action_taken = action_res.action_taken
            closed_flag = action_res.closed
            skipped_reason = action_res.skipped_reason
            partial_errors = list(action_res.partial_errors)
        else:
            processed_api_batch += 1
            eff_cls = decision.classification
            eff_canon = decision.canonical_issue
            action_taken = f"offline_{decision.classification}"
            closed_flag = False
            skipped_reason = decision.blocked_reason
            partial_errors = []

        if (closed_flag or action_taken == "dry_run_auto_closed") and eff_canon is not None:
            matcher.duplicate_links[num] = eff_canon
            old_raw = issues_by_num[num]
            new_labels = tuple(l for l in old_raw.labels if l not in ("Em análise", "Aguardando informações"))
            if LABEL_DUPLICATE not in new_labels:
                new_labels = (*new_labels, LABEL_DUPLICATE)
            new_raw = replace(old_raw, state="closed", state_reason="duplicate", labels=new_labels)
            issues_by_num[num] = new_raw
            new_parsed = replace(
                target_parsed,
                raw=new_raw,
                is_open=False,
                is_closed=True,
                is_duplicate_marked=True,
                resolution="duplicate",
            )
            parsed_by_num[num] = new_parsed
            matcher.by_number[num] = new_parsed
            cat_list = matcher.by_category.get(new_parsed.category, [])
            for idx, item in enumerate(cat_list):
                if item.raw.number == num:
                    cat_list[idx] = new_parsed
                    break

        is_rate_limited = _is_rate_limit_failure(skipped_reason, partial_errors)

        if can_persist_cache and (not is_rate_limited) and action_taken in TERMINAL_CACHE_ACTIONS:
            canon_state = issues_by_num[eff_canon].state if eff_canon in issues_by_num else None
            cache_state["evaluated"][str(num)] = {
                "updated_at": target_parsed.raw.updated_at,
                "fingerprint": target_parsed.content_fingerprint,
                "auto_close_enabled": config.auto_close_enabled,
                "decision": eff_cls,
                "canonical_issue": eff_canon,
                "canonical_state": canon_state,
                "action_taken": action_taken,
            }
            _save_cache(cache_path, cache_state)

        report_rows.append({
            "issue_number": num,
            "category": decision.category,
            "classification": eff_cls,
            "canonical_issue": eff_canon,
            "should_auto_close": closed_flag or (action_taken == "dry_run_auto_closed"),
            "reasons": list(decision.reasons),
            "candidates": [asdict(c) for c in decision.candidates],
            "action_taken": action_taken,
            "dry_run": config.dry_run,
            "closed": closed_flag,
            "skipped_reason": skipped_reason,
            "partial_errors": partial_errors,
        })

        if is_rate_limited:
            rate_limited_stop = True
            if can_persist_cache:
                _save_cache(cache_path, cache_state)
            break

    open_numbers = {num for num, raw_obj in issues_by_num.items() if raw_obj.state == "open"}
    if can_persist_cache:
        _prune_cache(cache_state, open_numbers, set(issues_by_num.keys()))
        if effective_mode == "backfill" and not batch_truncated and not rate_limited_stop:
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
    error_actions = {
        "api_error_skipped",
        "aborted_canonical_check_failed",
        "aborted_comment_failed",
        "partial_failure_before_close",
        "partial_auto_close_error",
        "comment_failed",
    }
    error_count = sum(
        1 for r in report_rows
        if (r["partial_errors"] or r["action_taken"] in error_actions)
        and not _is_rate_limit_failure(r.get("skipped_reason"), r.get("partial_errors", []))
    )
    api_calls = getattr(client, "api_calls_made", 0) if client is not None else 0

    if error_count > 0:
        status = "partial_error"
        completed = False
        recoverable = False
        exit_code = 2
    elif rate_limited_stop:
        status = "incomplete_rate_limited"
        completed = False
        recoverable = True
        exit_code = 3
    elif batch_truncated:
        status = "incomplete_batch"
        completed = False
        recoverable = True
        exit_code = 0
    else:
        status = "ok"
        completed = True
        recoverable = True
        exit_code = 0

    meta = {
        "status": status,
        "completed": completed,
        "recoverable": recoverable,
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
        "error_count": error_count,
        "batch_truncated": batch_truncated,
        "rate_limited_stop": rate_limited_stop,
        "api_calls_made": api_calls,
    }

    _write_summary(args.summary_file, meta, report_rows)
    print(json.dumps({**meta, "results": report_rows}, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(run_cli())
