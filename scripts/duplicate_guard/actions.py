# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Idempotent GitHub comment, label, and safe auto-close action executor."""

from __future__ import annotations

import re
import sys
from typing import Protocol

from .github_client import GitHubApiError, PermissionDeniedError, RateLimitError
from .models import (
    HUMAN_INTERVENTION_LABELS,
    INCOMPATIBLE_STATUS_LABELS_ON_DUPLICATE,
    LABEL_DUPLICATE,
    MAINTAINER_OVERRIDE_LABELS,
    TRUSTED_AUTHOR_ASSOCIATIONS,
    TRUSTED_BOT_LOGINS,
    ActionResult,
    GuardConfig,
    GuardDecision,
    RawIssue,
)
from .normalize import sanitize_for_markdown

MARKER_PREFIX = "<!-- nox-duplicate-guard:v1"
_MARKER_RE = re.compile(
    r"<!--\s*nox-duplicate-guard:v1\b([^>]*)-->",
    re.IGNORECASE,
)
_IGNORE_OVERRIDE_RE = re.compile(
    r"(?:<!--\s*nox-duplicate-guard:ignore\s*-->|/not-duplicate\b)",
    re.IGNORECASE,
)


class IssueApiProtocol(Protocol):
    """Subset of GitHubClient used by GuardActionExecutor (mockable in tests)."""

    def get_issue(self, issue_number: int) -> dict: ...
    def list_comments(self, issue_number: int) -> list[dict]: ...
    def create_comment(self, issue_number: int, body: str) -> dict: ...
    def update_comment(self, comment_id: int, body: str) -> dict: ...
    def add_labels(self, issue_number: int, labels: list[str]) -> None: ...
    def remove_label(self, issue_number: int, label_name: str) -> None: ...
    def close_issue(self, issue_number: int, *, state_reason: str = "duplicate") -> dict: ...


def build_marker(
    issue_number: int,
    decision: str,
    canonical_issue: int | None,
    action: str,
    fingerprint: str,
) -> str:
    """Build deterministic hidden HTML marker for idempotence and chain resolution."""
    canon_val = str(canonical_issue) if canonical_issue is not None else "none"
    return (
        f"<!-- nox-duplicate-guard:v1 issue={issue_number} "
        f"decision={decision} canonical={canon_val} action={action} fp={fingerprint} -->"
    )


def parse_marker(body: str) -> dict[str, str] | None:
    """Parse metadata fields from an existing Nox Duplicate Guard comment marker."""
    m = _MARKER_RE.search(body or "")
    if not m:
        return None
    attrs: dict[str, str] = {}
    for pair in m.group(1).strip().split():
        if "=" in pair:
            k, v = pair.split("=", 1)
            attrs[k.strip()] = v.strip()
    return attrs


def is_trusted_bot_comment(comment: dict) -> bool:
    """Return True only when comment was authored by the official GitHub Actions bot."""
    if not isinstance(comment, dict):
        return False
    user = comment.get("user")
    if not isinstance(user, dict):
        return False
    login = str(user.get("login") or "").strip().lower()
    user_type = str(user.get("type") or "").strip().lower()
    if login not in TRUSTED_BOT_LOGINS:
        return False
    return user_type == "bot" or login.endswith("[bot]")


def is_trusted_maintainer_comment(comment: dict) -> bool:
    """Return True only when comment was authored by a verified repo maintainer/collaborator."""
    if not isinstance(comment, dict):
        return False
    user = comment.get("user")
    if not isinstance(user, dict):
        return False
    login = str(user.get("login") or "").strip()
    user_type = str(user.get("type") or "").strip().lower()
    if not login or user_type == "bot" or login.lower().endswith("[bot]"):
        return False
    if user_type and user_type != "user":
        return False
    assoc = str(comment.get("author_association") or "").strip().upper()
    return assoc in TRUSTED_AUTHOR_ASSOCIATIONS


def find_existing_bot_comment(
    comments: list[dict],
    issue_number: int | None = None,
) -> tuple[dict | None, dict[str, str] | None]:
    """Return the latest trusted bot comment containing a valid marker for this issue."""
    for comment in reversed(comments):
        if not is_trusted_bot_comment(comment):
            continue
        meta = parse_marker(str(comment.get("body") or ""))
        if meta is None:
            continue
        if issue_number is not None and meta.get("issue") and meta.get("issue") != str(issue_number):
            continue
        return comment, meta
    return None, None


def has_manual_ignore_override(
    comments: list[dict],
    live_issue: RawIssue | None = None,
) -> bool:
    """Return True only when an authorized maintainer label or verified comment requests ignore."""
    if live_issue is not None:
        issue_labels = {l.strip().lower() for l in live_issue.labels}
        if issue_labels & {lbl.lower() for lbl in MAINTAINER_OVERRIDE_LABELS}:
            return True
    for comment in comments:
        if _IGNORE_OVERRIDE_RE.search(str(comment.get("body") or "")):
            if is_trusted_maintainer_comment(comment):
                return True
    return False


def render_comment_body(
    decision: GuardDecision,
    action: str,
    issues_by_number: dict[int, RawIssue],
) -> str:
    """Render polite, transparent, injection-safe Markdown comment in Portuguese."""
    marker = build_marker(
        decision.issue_number,
        decision.classification,
        decision.canonical_issue,
        action,
        decision.fingerprint,
    )

    if decision.classification == "no_match":
        return (
            f"{marker}\n"
            "Olá! Após a atualização desta solicitação, o **Nox Duplicate Guard** não identificou "
            "nenhuma correspondência duplicada pendente.\n\n"
            "Esta solicitação seguirá normalmente para triagem da equipe."
        )

    if decision.classification == "confirmed_duplicate" and action == "auto_closed" and decision.canonical_issue:
        canon_num = decision.canonical_issue
        canon_raw = issues_by_number.get(canon_num)
        canon_title = sanitize_for_markdown(canon_raw.title if canon_raw else f"Issue #{canon_num}", 110)
        reasons_lines = "\n".join(f"- {sanitize_for_markdown(r, 180)}" for r in decision.reasons[:3])
        return (
            f"{marker}\n"
            "Olá! O **Nox Duplicate Guard** identificou com alta confiança que esta solicitação é "
            "duplicada de um ticket já existente e realizou o encerramento formal automático.\n\n"
            f"**Ticket original:** #{canon_num} — {canon_title}\n\n"
            f"**Critérios identificados:**\n{reasons_lines}\n\n"
            f"Acompanhe o andamento pelo ticket #{canon_num} e adicione sua reação 👍 na primeira mensagem "
            "para ajudar na priorização.\n\n"
            "Se este encerramento automático tiver sido um engano e o problema for diferente, responda "
            "com mais detalhes técnicos ou solicite reabertura à equipe."
        )

    # Possible duplicate (or confirmed duplicate when auto-close is disabled/blocked)
    canon_num = decision.canonical_issue
    lines = [
        marker,
        "Olá! O **Nox Duplicate Guard** encontrou solicitação(ões) que podem estar relacionadas a este ticket.",
        "",
    ]
    if canon_num is not None:
        title_hint = ""
        canon_raw = issues_by_number.get(canon_num)
        if canon_raw:
            title_hint = f" — {sanitize_for_markdown(canon_raw.title, 100)}"
        lines.append(f"**Possível ticket original:** #{canon_num}{title_hint}")
        lines.append("")

    if decision.classification == "confirmed_duplicate" and decision.reasons:
        lines.append("**Critérios identificados:**")
        for r in decision.reasons[:3]:
            lines.append(f"- {sanitize_for_markdown(r, 180)}")
        lines.append("")

    if decision.candidates:
        lines.append("**Tickets relacionados:**")
        for cand in decision.candidates:
            raw_cand = issues_by_number.get(cand.canonical_number)
            safe_title = sanitize_for_markdown(raw_cand.title if raw_cand else f"Issue #{cand.canonical_number}", 100)
            state_badge = "Aberta" if cand.candidate_is_open else "Fechada"
            reason = sanitize_for_markdown(cand.reasons[0] if cand.reasons else "", 140)
            lines.append(f"- #{cand.canonical_number} ({state_badge}) — {safe_title}" + (f" · _{reason}_" if reason else ""))
        lines.append("")

    lines.extend([
        "Verifique se o problema é o mesmo. Caso seja, acompanhe a solicitação existente e adicione sua reação 👍 para apoiar a priorização.",
        "",
        "Se o problema for diferente, forneça detalhes adicionais para ajudar a equipe na análise.",
    ])
    return "\n".join(lines)


class GuardActionExecutor:
    """Applies idempotent, conservative GitHub actions for a GuardDecision."""

    def __init__(
        self,
        client: IssueApiProtocol,
        config: GuardConfig,
        issues_by_number: dict[int, RawIssue],
    ) -> None:
        self.client = client
        self.config = config
        self.issues_by_number = issues_by_number

    def apply(self, decision: GuardDecision) -> ActionResult:
        """Apply comment / label / close actions safely and idempotently."""
        num = decision.issue_number

        # Fetch live issue and comments to prevent stale-event races and detect human intervention.
        try:
            live_issue_dict = self.client.get_issue(num)
            live_issue = RawIssue.from_github_dict(live_issue_dict)
            comments = self.client.list_comments(num)
        except (RateLimitError, PermissionDeniedError, GitHubApiError) as exc:
            return ActionResult(
                issue_number=num,
                decision=decision.classification,
                canonical_issue=decision.canonical_issue,
                action_taken="api_error_skipped",
                dry_run=self.config.dry_run,
                skipped_reason=str(exc),
            )

        existing_comment, existing_meta = find_existing_bot_comment(comments, issue_number=num)

        if has_manual_ignore_override(comments, live_issue=live_issue):
            return ActionResult(
                issue_number=num,
                decision=decision.classification,
                canonical_issue=decision.canonical_issue,
                action_taken="skipped_manual_override",
                dry_run=self.config.dry_run,
                comment_id=existing_comment.get("id") if existing_comment else None,
                skipped_reason="manual_ignore_override",
            )

        # If no duplicate found and bot never commented before, do nothing.
        if decision.classification == "no_match":
            if existing_comment is None:
                return ActionResult(
                    issue_number=num,
                    decision="no_match",
                    canonical_issue=None,
                    action_taken="noop",
                    dry_run=self.config.dry_run,
                )
            if existing_meta and existing_meta.get("decision") == "no_match" and existing_meta.get("fp") == decision.fingerprint:
                return ActionResult(
                    issue_number=num,
                    decision="no_match",
                    canonical_issue=None,
                    action_taken="noop_already_updated",
                    dry_run=self.config.dry_run,
                    comment_id=existing_comment.get("id"),
                )
            # Issue was edited and is no longer considered a duplicate: update existing bot comment cleanly.
            body = render_comment_body(decision, "cleared", self.issues_by_number)
            return self._upsert_comment_only(num, decision, "cleared", body, existing_comment)

        target_canon_str = str(decision.canonical_issue) if decision.canonical_issue is not None else "none"

        # If this issue was already auto-closed by the guard for the exact same decision/canonical/fp
        # and remains closed with the official Duplicado label, treat re-runs as pure no-op.
        if (
            existing_meta
            and existing_meta.get("action") == "auto_closed"
            and existing_meta.get("decision") == decision.classification
            and existing_meta.get("canonical") == target_canon_str
            and existing_meta.get("fp") == decision.fingerprint
            and live_issue.state == "closed"
            and LABEL_DUPLICATE in live_issue.labels
        ):
            return ActionResult(
                issue_number=num,
                decision=decision.classification,
                canonical_issue=decision.canonical_issue,
                action_taken="noop_idempotent",
                dry_run=self.config.dry_run,
                comment_id=existing_comment.get("id") if existing_comment else None,
            )

        # Determine whether auto-close is permitted on live state
        can_auto_close, block_reason = self._check_live_auto_close_safety(
            decision, live_issue, existing_meta
        )

        intended_action = "auto_closed" if can_auto_close else "commented"

        # Idempotency check: skip writes if marker matches current decision/canonical/action/fp
        if existing_meta and (
            existing_meta.get("decision") == decision.classification
            and existing_meta.get("canonical") == target_canon_str
            and existing_meta.get("action") == intended_action
            and existing_meta.get("fp") == decision.fingerprint
        ):
            return ActionResult(
                issue_number=num,
                decision=decision.classification,
                canonical_issue=decision.canonical_issue,
                action_taken="noop_idempotent",
                dry_run=self.config.dry_run,
                comment_id=existing_comment.get("id") if existing_comment else None,
                skipped_reason=block_reason,
            )

        body = render_comment_body(decision, intended_action, self.issues_by_number)

        if self.config.dry_run:
            return ActionResult(
                issue_number=num,
                decision=decision.classification,
                canonical_issue=decision.canonical_issue,
                action_taken=f"dry_run_{intended_action}",
                dry_run=True,
                comment_id=existing_comment.get("id") if existing_comment else None,
                labels_added=(LABEL_DUPLICATE,) if can_auto_close else (),
                closed=can_auto_close,
                skipped_reason=block_reason,
            )

        if not can_auto_close:
            return self._upsert_comment_only(
                num, decision, "commented", body, existing_comment, skipped_reason=block_reason
            )

        # Perform formal duplicate closure transactionally:
        # 1. Verify canonical live -> 2. Register justification comment -> 3. Apply label Duplicado ->
        # 4. Close issue (rollback label on failure) -> 5. Remove initial triage status labels.
        partial_errors: list[str] = []
        labels_added: list[str] = []
        labels_removed: list[str] = []
        comment_id: int | None = existing_comment.get("id") if existing_comment else None
        closed_ok = False

        # 1. Verify canonical issue is STILL open on live API right before mutating anything
        try:
            assert decision.canonical_issue is not None
            live_canon = RawIssue.from_github_dict(self.client.get_issue(decision.canonical_issue))
            if live_canon.state != "open" or LABEL_DUPLICATE in live_canon.labels:
                fallback_body = render_comment_body(decision, "commented", self.issues_by_number)
                return self._upsert_comment_only(
                    num,
                    decision,
                    "commented",
                    fallback_body,
                    existing_comment,
                    skipped_reason="live_canonical_no_longer_open",
                )
        except (RateLimitError, PermissionDeniedError, GitHubApiError) as exc:
            print(f"[Nox Duplicate Guard] Issue #{num}: falha ao verificar canônico ao vivo: {exc}", file=sys.stderr)
            return ActionResult(
                issue_number=num,
                decision=decision.classification,
                canonical_issue=decision.canonical_issue,
                action_taken="aborted_canonical_check_failed",
                dry_run=False,
                skipped_reason=str(exc),
            )

        # 2. Create or update explanatory comment BEFORE adding resolution label or closing
        try:
            if existing_comment and existing_comment.get("id"):
                updated = self.client.update_comment(int(existing_comment["id"]), body)
                comment_id = updated.get("id", existing_comment.get("id"))
            else:
                created = self.client.create_comment(num, body)
                comment_id = created.get("id")
        except (RateLimitError, PermissionDeniedError, GitHubApiError) as exc:
            print(f"[Nox Duplicate Guard] Issue #{num}: falha ao registrar justificativa antes de fechar: {exc}", file=sys.stderr)
            return ActionResult(
                issue_number=num,
                decision=decision.classification,
                canonical_issue=decision.canonical_issue,
                action_taken="aborted_comment_failed",
                dry_run=False,
                comment_id=comment_id,
                partial_errors=(f"comment:{exc}",),
            )

        # 3. Add official label Duplicado
        added_dup_now = False
        if LABEL_DUPLICATE not in live_issue.labels:
            try:
                self.client.add_labels(num, [LABEL_DUPLICATE])
                labels_added.append(LABEL_DUPLICATE)
                added_dup_now = True
            except (RateLimitError, PermissionDeniedError, GitHubApiError) as exc:
                print(f"[Nox Duplicate Guard] Issue #{num}: falha ao aplicar label Duplicado: {exc}", file=sys.stderr)
                partial_errors.append(f"add_label:{exc}")
                self._downgrade_comment_after_failure(num, comment_id, decision, partial_errors)
                return ActionResult(
                    issue_number=num,
                    decision=decision.classification,
                    canonical_issue=decision.canonical_issue,
                    action_taken="partial_failure_before_close",
                    dry_run=False,
                    comment_id=comment_id,
                    partial_errors=tuple(partial_errors),
                )

        # 4. Close issue with state_reason='duplicate' (rollback Duplicado label if close fails)
        try:
            self.client.close_issue(num, state_reason="duplicate")
            closed_ok = True
        except (RateLimitError, PermissionDeniedError, GitHubApiError) as exc:
            print(f"[Nox Duplicate Guard] Issue #{num}: falha ao fechar issue como duplicada: {exc}", file=sys.stderr)
            partial_errors.append(f"close:{exc}")
            if added_dup_now:
                try:
                    self.client.remove_label(num, LABEL_DUPLICATE)
                    labels_added.clear()
                except (RateLimitError, PermissionDeniedError, GitHubApiError) as rb_exc:
                    print(f"[Nox Duplicate Guard] Issue #{num}: falha ao reverter label Duplicado: {rb_exc}", file=sys.stderr)
                    partial_errors.append(f"rollback_label:{rb_exc}")
            self._downgrade_comment_after_failure(num, comment_id, decision, partial_errors)
            return ActionResult(
                issue_number=num,
                decision=decision.classification,
                canonical_issue=decision.canonical_issue,
                action_taken="partial_failure_before_close",
                dry_run=False,
                comment_id=comment_id,
                labels_added=tuple(labels_added),
                closed=False,
                partial_errors=tuple(partial_errors),
            )

        # 5. Remove incompatible triage status labels ONLY after formal closure succeeds
        for lbl in INCOMPATIBLE_STATUS_LABELS_ON_DUPLICATE:
            if lbl in live_issue.labels:
                try:
                    self.client.remove_label(num, lbl)
                    labels_removed.append(lbl)
                except (RateLimitError, PermissionDeniedError, GitHubApiError) as exc:
                    print(f"[Nox Duplicate Guard] Issue #{num}: falha ao remover label '{lbl}': {exc}", file=sys.stderr)
                    partial_errors.append(f"remove_label({lbl}):{exc}")

        return ActionResult(
            issue_number=num,
            decision=decision.classification,
            canonical_issue=decision.canonical_issue,
            action_taken="auto_closed" if closed_ok else "partial_auto_close_error",
            dry_run=False,
            comment_id=comment_id,
            labels_added=tuple(labels_added),
            labels_removed=tuple(labels_removed),
            closed=closed_ok,
            partial_errors=tuple(partial_errors),
        )

    def _downgrade_comment_after_failure(
        self,
        issue_number: int,
        comment_id: int | None,
        decision: GuardDecision,
        partial_errors: list[str],
    ) -> None:
        if not comment_id:
            return
        try:
            fallback_body = render_comment_body(decision, "commented", self.issues_by_number)
            self.client.update_comment(int(comment_id), fallback_body)
        except (RateLimitError, PermissionDeniedError, GitHubApiError) as exc:
            print(f"[Nox Duplicate Guard] Issue #{issue_number}: falha ao ajustar comentário após erro parcial: {exc}", file=sys.stderr)
            partial_errors.append(f"downgrade_comment:{exc}")

    def _check_live_auto_close_safety(
        self,
        decision: GuardDecision,
        live_issue: RawIssue,
        existing_meta: dict[str, str] | None,
    ) -> tuple[bool, str | None]:
        if not decision.should_auto_close:
            return False, decision.blocked_reason
        if live_issue.state != "open":
            return False, "live_issue_already_closed"
        issue_labels_lower = {l.strip().lower() for l in live_issue.labels}
        if issue_labels_lower & {lbl.lower() for lbl in MAINTAINER_OVERRIDE_LABELS}:
            return False, "manual_ignore_override"
        # Protect maintainer triage/resolution labels (except stale Duplicado left if an earlier rollback failed)
        protected_labels = HUMAN_INTERVENTION_LABELS - {LABEL_DUPLICATE}
        if set(live_issue.labels) & protected_labels:
            return False, "human_intervention_label_present"
        if live_issue.state_reason == "reopened":
            return False, "previously_auto_closed_then_reopened"
        # Respect human reopen: if bot previously auto-closed this issue and it's open again, do NOT re-close!
        if existing_meta and existing_meta.get("action") == "auto_closed":
            return False, "previously_auto_closed_then_reopened"
        if decision.canonical_issue is None or decision.canonical_issue == decision.issue_number:
            return False, "invalid_canonical_issue"
        return True, None

    def _upsert_comment_only(
        self,
        num: int,
        decision: GuardDecision,
        action_label: str,
        body: str,
        existing_comment: dict | None,
        *,
        skipped_reason: str | None = None,
    ) -> ActionResult:
        if self.config.dry_run:
            return ActionResult(
                issue_number=num,
                decision=decision.classification,
                canonical_issue=decision.canonical_issue,
                action_taken=f"dry_run_{action_label}",
                dry_run=True,
                comment_id=existing_comment.get("id") if existing_comment else None,
                skipped_reason=skipped_reason,
            )
        try:
            if existing_comment and existing_comment.get("id"):
                cid = int(existing_comment["id"])
                self.client.update_comment(cid, body)
            else:
                created = self.client.create_comment(num, body)
                cid = created.get("id")
            return ActionResult(
                issue_number=num,
                decision=decision.classification,
                canonical_issue=decision.canonical_issue,
                action_taken=action_label,
                dry_run=False,
                comment_id=cid,
                skipped_reason=skipped_reason,
            )
        except (RateLimitError, PermissionDeniedError, GitHubApiError) as exc:
            return ActionResult(
                issue_number=num,
                decision=decision.classification,
                canonical_issue=decision.canonical_issue,
                action_taken="comment_failed",
                dry_run=False,
                comment_id=existing_comment.get("id") if existing_comment else None,
                skipped_reason=skipped_reason,
                partial_errors=(f"comment:{exc}",),
            )
