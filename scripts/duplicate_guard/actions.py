# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Idempotent GitHub comment, label, and safe auto-close action executor."""

from __future__ import annotations

import re
import sys
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol

from .github_client import GitHubApiError, PermissionDeniedError, RateLimitError
from .matcher import DuplicateMatcher, evaluate_comment_clarifications
from .models import (
    ALL_CATEGORIES,
    CATEGORY_NEW_EXTENSION,
    HUMAN_INTERVENTION_LABELS,
    INCOMPATIBLE_STATUS_LABELS_ON_DUPLICATE,
    INTENT_CREATE_EXTENSION,
    LABEL_DONE,
    LABEL_DUPLICATE,
    MAINTAINER_OVERRIDE_LABELS,
    OFFICIAL_CATEGORY_LABELS,
    OFFICIAL_RESOLUTION_LABELS,
    TRUSTED_AUTHOR_ASSOCIATIONS,
    TRUSTED_BOT_LOGINS,
    ActionResult,
    CatalogExtension,
    ExtensionCatalog,
    GuardConfig,
    GuardDecision,
    ParsedIssue,
    RawIssue,
)
from .normalize import sanitize_for_markdown
from .parser import parse_catalog_entries, parse_issue

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


def _issue_url(issue_number: int, issues_by_number: dict[int, RawIssue], repo: str) -> str:
    raw = issues_by_number.get(issue_number)
    if raw and raw.html_url and raw.html_url.startswith("https://"):
        return raw.html_url
    return f"https://github.com/{repo}/issues/{issue_number}"


def render_comment_body(
    decision: GuardDecision,
    action: str,
    issues_by_number: dict[int, RawIssue],
    repo: str = "ProjectNox-org/project-nox-requests",
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
        details = ""
        if decision.reasons and decision.match_kind == "distinct_objective":
            details = "\n\n**Análise semântica:**\n" + "\n".join(
                f"- {sanitize_for_markdown(r, 220)}" for r in decision.reasons[:2]
            )
        mismatch_note = ""
        if decision.category_mismatch and decision.inferred_category in OFFICIAL_CATEGORY_LABELS:
            form_label = OFFICIAL_CATEGORY_LABELS[decision.inferred_category]
            mismatch_note = (
                f"\n\n> **Observação de formulário:** o conteúdo da solicitação indica objetivo compatível com **{form_label}**. "
                "Esta solicitação não foi marcada como duplicata e permanece aberta."
            )
        return (
            f"{marker}\n"
            "Olá! Após a avaliação semântica atualizada desta solicitação, o **Nox Duplicate Guard** identificou "
            "que este pedido **não é duplicata** para fechamento automático e permanece **aberto** para triagem normal da equipe."
            f"{details}{mismatch_note}"
        )

    if decision.classification == "confirmed_duplicate" and action == "auto_closed":
        reasons_lines = "\n".join(f"- {sanitize_for_markdown(r, 220)}" for r in decision.reasons[:3])
        if decision.match_kind == "already_fulfilled_creation" and decision.catalog_extension:
            cat = decision.catalog_extension
            ext_name = sanitize_for_markdown(cat.get("name") or "extensão", 100)
            pkg = sanitize_for_markdown(cat.get("pkg") or "", 100)
            ver = sanitize_for_markdown(cat.get("version") or "", 30)
            langs = ", ".join(cat.get("languages") or []) or "N/D"
            cat_repo = cat.get("catalog_repo") or "ProjectNox-org/manga-extensoes"
            cat_url = f"https://github.com/{cat_repo}"
            lines = [
                marker,
                "Olá! O **Nox Duplicate Guard** verificou que a extensão solicitada **já existe no catálogo oficial do Project Nox** e este pedido de criação já foi atendido.",
                "",
                f"**Extensão publicada:** {ext_name} (`{pkg}`" + (f" · v{ver}" if ver else "") + f") · **Idioma(s):** {langs}",
                f"**Repositório oficial:** {cat_url}",
            ]
            if decision.canonical_issue:
                canon_num = decision.canonical_issue
                canon_url = _issue_url(canon_num, issues_by_number, repo)
                canon_raw = issues_by_number.get(canon_num)
                canon_title = sanitize_for_markdown(canon_raw.title if canon_raw else f"Issue #{canon_num}", 110)
                lines.append(f"**Ticket original:** [#{canon_num}]({canon_url}) — {canon_title}")
            lines.extend([
                "",
                f"**Critérios identificados:**\n{reasons_lines}",
                "",
                "Se a extensão existente apresentar algum erro, mudança de domínio ou precisar de suporte a outro idioma/funcionalidade, "
                "abra uma solicitação usando o formulário específico (**Problema na extensão** ou **Sugestão de melhoria**), ou responda explicando a necessidade.",
            ])
            return "\n".join(lines)

        if decision.canonical_issue:
            canon_num = decision.canonical_issue
            canon_url = _issue_url(canon_num, issues_by_number, repo)
            canon_raw = issues_by_number.get(canon_num)
            canon_title = sanitize_for_markdown(canon_raw.title if canon_raw else f"Issue #{canon_num}", 110)
            return (
                f"{marker}\n"
                "Olá! O **Nox Duplicate Guard** identificou com alta confiança que esta solicitação é "
                "duplicada equivalente de um ticket já existente e realizou o encerramento formal automático.\n\n"
                f"**Ticket original:** [#{canon_num}]({canon_url}) — {canon_title}\n\n"
                f"**Por que são equivalentes:**\n{reasons_lines}\n\n"
                f"Acompanhe o andamento pelo ticket [#{canon_num}]({canon_url}) e adicione sua reação 👍 na primeira mensagem "
                "para ajudar na priorização.\n\n"
                "Se este encerramento automático tiver sido um engano ou tratar de um problema/objetivo diferente, responda "
                "com mais detalhes técnicos ou solicite reabertura à equipe."
            )

    # Possible duplicate (or confirmed duplicate when auto-close is disabled/blocked)
    canon_num = decision.canonical_issue
    lines = [
        marker,
        "Olá! O **Nox Duplicate Guard** encontrou solicitação(ões) relacionadas, mas manteve este ticket **aberto** para avaliação segura.",
        "",
    ]
    if canon_num is not None:
        canon_url = _issue_url(canon_num, issues_by_number, repo)
        title_hint = ""
        canon_raw = issues_by_number.get(canon_num)
        if canon_raw:
            title_hint = f" — {sanitize_for_markdown(canon_raw.title, 100)}"
        lines.append(f"**Ticket relacionado:** [#{canon_num}]({canon_url}){title_hint}")
        lines.append("")

    if decision.reasons:
        lines.append("**Motivo da associação:**")
        for r in decision.reasons[:3]:
            lines.append(f"- {sanitize_for_markdown(r, 200)}")
        lines.append("")

    if decision.category_mismatch and decision.inferred_category in OFFICIAL_CATEGORY_LABELS:
        form_label = OFFICIAL_CATEGORY_LABELS[decision.inferred_category]
        lines.append(
            f"> **Observação de formulário:** o conteúdo da solicitação indica objetivo compatível com **{form_label}**. "
            "Este ticket não foi tratado como duplicata simples e permanece aberto para triagem."
        )
        lines.append("")

    all_closed_candidates = False
    if decision.candidates:
        lines.append("**Tickets relacionados:**")
        open_states: list[bool] = []
        for cand in decision.candidates:
            open_states.append(cand.candidate_is_open)
            cand_url = _issue_url(cand.canonical_number, issues_by_number, repo)
            raw_cand = issues_by_number.get(cand.canonical_number)
            safe_title = sanitize_for_markdown(raw_cand.title if raw_cand else f"Issue #{cand.canonical_number}", 100)
            state_badge = "Aberta" if cand.candidate_is_open else "Fechada"
            reason = sanitize_for_markdown(cand.reasons[0] if cand.reasons else "", 160)
            lines.append(f"- [#{cand.canonical_number}]({cand_url}) ({state_badge}) — {safe_title}" + (f" · _{reason}_" if reason else ""))
        lines.append("")
        all_closed_candidates = bool(open_states) and not any(open_states)
    elif canon_num is not None and canon_num in issues_by_number:
        all_closed_candidates = issues_by_number[canon_num].state == "closed"

    if all_closed_candidates:
        lines.extend([
            "O ticket relacionado acima já se encontra encerrado. Esta associação é apenas informativa (pode tratar-se de regressão, melhoria posterior ou objetivo distinto).",
            "",
            "**Esta solicitação permanece aberta** para análise da equipe; forneça detalhes adicionais se desejar complementar o contexto.",
        ])
    else:
        lines.extend([
            "Verifique se o problema ou objetivo é exatamente o mesmo. Caso seja, acompanhe a solicitação aberta existente e adicione sua reação 👍 para apoiar a priorização.",
            "",
            "Se o objetivo ou o problema for diferente, esta solicitação permanecerá aberta para análise da equipe.",
        ])
    return "\n".join(lines)


class GuardActionExecutor:
    """Applies idempotent, conservative GitHub actions for a GuardDecision."""

    def __init__(
        self,
        client: IssueApiProtocol,
        config: GuardConfig,
        issues_by_number: dict[int, RawIssue],
        *,
        repo: str = "ProjectNox-org/project-nox-requests",
        matcher: DuplicateMatcher | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.issues_by_number = issues_by_number
        self.repo = repo
        self.matcher = matcher
        self._known_extensions: frozenset[str] = (
            frozenset(
                p.compact_name for p in matcher.by_number.values() if p.compact_name and len(p.compact_name) >= 4
            )
            if matcher is not None
            else frozenset()
        )

    def _verify_live_catalog_fulfillment(
        self,
        live_parsed: ParsedIssue,
    ) -> tuple[bool, str | None, CatalogExtension | None]:
        """Confirm live official catalog publication directly using unified source-level identity and language coverage."""
        if live_parsed.category != CATEGORY_NEW_EXTENSION:
            return False, "live_catalog_invalid_category", None
        if live_parsed.ext_type not in ("Mangá", "Anime"):
            return False, "live_catalog_missing_ext_type", None
        if not (live_parsed.url and live_parsed.url.is_valid_domain and live_parsed.compact_name):
            return False, "live_catalog_incomplete_issue_identity", None
        if not live_parsed.target_languages or any(l.startswith("unknown:") for l in live_parsed.target_languages):
            return False, "live_catalog_unverified_language", None
        if (
            live_parsed.semantic_intent != INTENT_CREATE_EXTENSION
            or live_parsed.extension_already_exists
            or live_parsed.category_mismatch
            or live_parsed.has_substantive_observations
            or bool(live_parsed.symptom_topics)
            or bool(live_parsed.error_signatures - {"cloudflare"})
        ):
            return False, "live_catalog_non_pure_creation", None

        target_repo = (
            "ProjectNox-org/manga-extensoes"
            if live_parsed.ext_type == "Mangá"
            else "ProjectNox-org/anime-extensoes"
        )
        raw_entries: list[dict] | None = None
        if hasattr(self.client, "fetch_catalog_json"):
            try:
                raw_entries = self.client.fetch_catalog_json(target_repo, "index.min.json")  # type: ignore[attr-defined]
            except RateLimitError:
                raise
            except (PermissionDeniedError, GitHubApiError, ValueError, TypeError) as exc:
                return False, f"live_catalog_fetch_failed:{exc}", None
            except Exception as exc:
                return False, f"live_catalog_fetch_failed:{exc}", None
        elif hasattr(self.client, "list_catalog_extensions"):
            try:
                payload = self.client.list_catalog_extensions(strict=True)  # type: ignore[attr-defined]
            except TypeError:
                try:
                    payload = self.client.list_catalog_extensions()  # type: ignore[attr-defined]
                except RateLimitError:
                    raise
                except Exception as exc:
                    return False, f"live_catalog_fetch_failed:{exc}", None
            except RateLimitError:
                raise
            except Exception as exc:
                return False, f"live_catalog_fetch_failed:{exc}", None
            key = "manga" if live_parsed.ext_type == "Mangá" else "anime"
            raw_entries = (payload or {}).get(key) if isinstance(payload, dict) else None
        else:
            return False, "live_catalog_client_unsupported", None

        if not isinstance(raw_entries, list):
            return False, "live_catalog_invalid_payload", None

        live_entries = parse_catalog_entries(raw_entries, live_parsed.ext_type, target_repo)
        live_catalog = ExtensionCatalog(live_entries)
        cat_ok, cat_reason, cat_ext, matched_srcs = live_catalog.verify_issue_fulfillment(live_parsed)
        if not cat_ok or cat_ext is None or not matched_srcs:
            return False, cat_reason or "live_catalog_extension_missing_or_language_mismatch", None
        return True, None, cat_ext

    def _downgrade_unverified_catalog_decision(
        self,
        live_parsed: ParsedIssue,
        decision: GuardDecision,
        reason: str,
    ) -> GuardDecision:
        if self.matcher is not None:
            no_cat_matcher = DuplicateMatcher(
                list(self.matcher.by_number.values()),
                self.config,
                duplicate_links=self.matcher.duplicate_links,
                catalog=ExtensionCatalog(),
            )
            fallback = no_cat_matcher.evaluate(live_parsed)
            if fallback.classification == "confirmed_duplicate" and fallback.should_auto_close:
                return fallback
            return replace(
                fallback,
                should_auto_close=False,
                blocked_reason=reason,
                reasons=(
                    *fallback.reasons,
                    f"Revalidação ao vivo do catálogo oficial impediu encerramento automático ({reason}).",
                ),
            )
        new_cls = "possible_duplicate" if decision.canonical_issue is not None else "no_match"
        return replace(
            decision,
            classification=new_cls,  # type: ignore[arg-type]
            should_auto_close=False,
            blocked_reason=reason,
            reasons=(
                *decision.reasons,
                f"Revalidação ao vivo do catálogo oficial impediu encerramento automático ({reason}).",
            ),
        )

    def _fetch_live_canonical(self, canonical_num: int | None) -> tuple[RawIssue | None, str | None]:
        """Fetch live canonical issue state, distinguishing rate limits from 404/inaccessible issues."""
        if canonical_num is None:
            return None, None
        try:
            raw_dict = self.client.get_issue(canonical_num)
            return RawIssue.from_github_dict(raw_dict), None
        except RateLimitError:
            raise
        except (PermissionDeniedError, GitHubApiError, KeyError) as exc:
            return None, str(exc)

    def _revalidate_live_decision(
        self,
        decision: GuardDecision,
        live_issue: RawIssue,
        comments: list[dict],
    ) -> tuple[GuardDecision, ParsedIssue, ParsedIssue | None, str | None]:
        """Re-parse target and canonical live, re-evaluate if edited, and enforce comment clarifications."""
        live_parsed = parse_issue(live_issue, known_extensions=self._known_extensions)
        canon_raw: RawIssue | None = None
        canon_parsed: ParsedIssue | None = None
        canon_err: str | None = None

        if decision.canonical_issue is not None:
            canon_raw, canon_err = self._fetch_live_canonical(decision.canonical_issue)
            if canon_raw is not None:
                canon_parsed = parse_issue(canon_raw, known_extensions=self._known_extensions)

        target_edited = live_parsed.content_fingerprint != decision.fingerprint
        canon_changed = False
        if canon_err is not None and decision.match_kind != "already_fulfilled_creation":
            canon_changed = True
        elif canon_parsed is not None:
            if self.matcher is not None and decision.canonical_issue in self.matcher.by_number:
                prev_canon = self.matcher.by_number[decision.canonical_issue]
                if canon_parsed.content_fingerprint != prev_canon.content_fingerprint:
                    canon_changed = True
            if decision.match_kind != "already_fulfilled_creation" and (
                canon_raw.state != "open"  # type: ignore[union-attr]
                or LABEL_DUPLICATE in canon_raw.labels  # type: ignore[union-attr]
                or bool(set(canon_raw.labels) & OFFICIAL_RESOLUTION_LABELS)  # type: ignore[union-attr]
            ):
                canon_changed = True

        active_decision = decision
        if target_edited or canon_changed:
            if self.matcher is not None:
                self.issues_by_number[live_issue.number] = live_issue
                self.matcher.reindex_issue(live_parsed)
                if canon_raw is not None and canon_parsed is not None:
                    self.issues_by_number[canon_raw.number] = canon_raw
                    self.matcher.reindex_issue(canon_parsed)
                active_decision = self.matcher.evaluate(live_parsed)
                if active_decision.canonical_issue != decision.canonical_issue:
                    if active_decision.canonical_issue is not None:
                        new_c_raw, new_c_err = self._fetch_live_canonical(active_decision.canonical_issue)
                        if new_c_raw is not None:
                            canon_raw = new_c_raw
                            canon_parsed = parse_issue(new_c_raw, known_extensions=self._known_extensions)
                            self.issues_by_number[new_c_raw.number] = new_c_raw
                            self.matcher.reindex_issue(canon_parsed)
                            if (
                                canon_parsed.content_fingerprint != decision.fingerprint
                                and active_decision.match_kind != "already_fulfilled_creation"
                                and (new_c_raw.state != "open" or LABEL_DUPLICATE in new_c_raw.labels)
                            ):
                                active_decision = replace(
                                    active_decision,
                                    classification="possible_duplicate",
                                    should_auto_close=False,
                                    blocked_reason="live_canonical_no_longer_open",
                                )
                        elif new_c_err and active_decision.match_kind != "already_fulfilled_creation":
                            canon_parsed = None
                            active_decision = replace(
                                active_decision,
                                classification="possible_duplicate",
                                should_auto_close=False,
                                blocked_reason="canonical_inaccessible_or_404",
                            )
                    else:
                        canon_parsed = None
                elif canon_err is not None and active_decision.classification == "confirmed_duplicate" and active_decision.match_kind != "already_fulfilled_creation":
                    active_decision = replace(
                        active_decision,
                        classification="possible_duplicate",
                        should_auto_close=False,
                        blocked_reason="canonical_inaccessible_or_404",
                    )
            else:
                stale_reason = "target_issue_edited_before_apply" if target_edited else "canonical_changed_before_apply"
                if canon_err is not None and decision.match_kind != "already_fulfilled_creation":
                    stale_reason = "canonical_inaccessible_or_404"
                if active_decision.classification == "confirmed_duplicate":
                    active_decision = replace(
                        active_decision,
                        classification="possible_duplicate",
                        should_auto_close=False,
                        blocked_reason=stale_reason,
                        reasons=(*active_decision.reasons, f"Revalidação ao vivo impediu fechamento automático ({stale_reason})."),
                    )

        # Fallback canonical ParsedIssue lookup if not yet parsed
        if canon_parsed is None and active_decision.canonical_issue is not None:
            if self.matcher is not None and active_decision.canonical_issue in self.matcher.by_number:
                canon_parsed = self.matcher.by_number[active_decision.canonical_issue]
            elif active_decision.canonical_issue in self.issues_by_number:
                canon_parsed = parse_issue(
                    self.issues_by_number[active_decision.canonical_issue],
                    known_extensions=self._known_extensions,
                )

        # Asymmetric comment clarifications (author/maintainer only): can ONLY downgrade/block, never promote
        if active_decision.classification == "confirmed_duplicate":
            clarif_blocked, clarif_reason = evaluate_comment_clarifications(
                live_parsed,
                canon_parsed,
                comments,
                catalog_ext_summary=active_decision.catalog_extension,
                match_kind=active_decision.match_kind,
            )
            if clarif_blocked:
                active_decision = replace(
                    active_decision,
                    classification="possible_duplicate",
                    should_auto_close=False,
                    blocked_reason=clarif_reason,
                    reasons=(
                        *active_decision.reasons,
                        f"Esclarecimento do autor/mantenedor nos comentários preservou o ticket aberto ({clarif_reason}).",
                    ),
                )

        # Live catalog confirmation when auto-close depends on already_fulfilled_creation
        if (
            active_decision.classification == "confirmed_duplicate"
            and active_decision.match_kind == "already_fulfilled_creation"
            and active_decision.should_auto_close
        ):
            cat_ok, cat_reason, cat_ext = self._verify_live_catalog_fulfillment(live_parsed)
            if not cat_ok or cat_ext is None:
                active_decision = self._downgrade_unverified_catalog_decision(
                    live_parsed,
                    active_decision,
                    cat_reason or "live_catalog_unverified",
                )
            else:
                active_decision = replace(active_decision, catalog_extension=cat_ext.to_summary_dict())

        return active_decision, live_parsed, canon_parsed, ("live_revalidated" if (target_edited or canon_changed) else None)

    def _verify_concurrent_barrier(
        self,
        num: int,
        expected_fp: str,
        decision: GuardDecision,
        canon_parsed: ParsedIssue | None,
        own_comment_id: int | None,
        allowed_resolution_label: str | None = None,
    ) -> tuple[bool, str | None, RawIssue | None]:
        """Re-fetch target, comments, and canonical right before label/close to prevent TOCTOU races."""
        try:
            fresh_raw = RawIssue.from_github_dict(self.client.get_issue(num))
            fresh_comments = self.client.list_comments(num)
        except (RateLimitError, PermissionDeniedError, GitHubApiError) as exc:
            return False, f"concurrent_recheck_failed:{exc}", None

        if fresh_raw.state != "open":
            return False, "issue_already_closed_concurrently", fresh_raw
        if has_manual_ignore_override(fresh_comments, live_issue=fresh_raw):
            return False, "manual_override_added_concurrently", fresh_raw

        protected = HUMAN_INTERVENTION_LABELS - {LABEL_DUPLICATE}
        if allowed_resolution_label:
            protected = protected - {allowed_resolution_label}
        if set(fresh_raw.labels) & protected:
            return False, "human_intervention_label_added_concurrently", fresh_raw

        fresh_parsed = parse_issue(fresh_raw, known_extensions=self._known_extensions)
        if fresh_parsed.content_fingerprint != expected_fp:
            return False, "target_edited_during_execution", fresh_raw

        # Check if another concurrent bot run already wrote an auto_closed comment
        latest_bot, latest_meta = find_existing_bot_comment(fresh_comments, issue_number=num)
        if latest_bot and own_comment_id is not None and int(latest_bot.get("id") or 0) != int(own_comment_id):
            if latest_meta and latest_meta.get("action") == "auto_closed":
                return False, "concurrent_bot_execution_detected", fresh_raw

        live_parsed_curr = fresh_parsed
        clarif_blocked, clarif_reason = evaluate_comment_clarifications(
            live_parsed_curr,
            canon_parsed,
            fresh_comments,
            catalog_ext_summary=decision.catalog_extension,
            match_kind=decision.match_kind,
        )
        if clarif_blocked:
            return False, f"clarification_added_during_execution:{clarif_reason}", fresh_raw

        if decision.match_kind == "already_fulfilled_creation":
            try:
                cat_ok, cat_reason, _ = self._verify_live_catalog_fulfillment(fresh_parsed)
            except RateLimitError as rl_exc:
                return False, f"GitHub API rate limit during live catalog recheck: {rl_exc}", fresh_raw
            if not cat_ok:
                return False, f"catalog_changed_before_close:{cat_reason}", fresh_raw

        if decision.match_kind != "already_fulfilled_creation" and decision.canonical_issue is not None:
            fresh_canon, canon_err = self._fetch_live_canonical(decision.canonical_issue)
            if fresh_canon is None or canon_err is not None:
                return False, "canonical_inaccessible_during_execution", fresh_raw
            if (
                fresh_canon.state != "open"
                or LABEL_DUPLICATE in fresh_canon.labels
                or bool(set(fresh_canon.labels) & OFFICIAL_RESOLUTION_LABELS)
            ):
                return False, "canonical_closed_or_marked_during_execution", fresh_raw
            if canon_parsed is not None:
                fresh_canon_parsed = parse_issue(fresh_canon, known_extensions=self._known_extensions)
                if fresh_canon_parsed.content_fingerprint != canon_parsed.content_fingerprint:
                    return False, "canonical_edited_during_execution", fresh_raw

        return True, None, fresh_raw

    def _ensure_closed_duplicate_labels(
        self,
        num: int,
        live_issue: RawIssue,
        target_res_label: str,
    ) -> ActionResult | None:
        """Sanitize labels on an already-closed duplicate issue without touching category/type labels."""
        has_any_resolution = bool(set(live_issue.labels) & OFFICIAL_RESOLUTION_LABELS)
        missing_dup = (not has_any_resolution) and (target_res_label not in live_issue.labels)
        stale_status = [lbl for lbl in INCOMPATIBLE_STATUS_LABELS_ON_DUPLICATE if lbl in live_issue.labels]
        if not missing_dup and not stale_status:
            return None
        if self.config.dry_run:
            return ActionResult(
                issue_number=num,
                decision="confirmed_duplicate",
                canonical_issue=None,
                action_taken="dry_run_sanitized_closed_labels",
                dry_run=True,
                labels_added=(target_res_label,) if missing_dup else (),
                labels_removed=tuple(stale_status),
                closed=True,
            )
        added: list[str] = []
        removed: list[str] = []
        errors: list[str] = []
        if missing_dup:
            try:
                self.client.add_labels(num, [target_res_label])
                added.append(target_res_label)
            except (RateLimitError, PermissionDeniedError, GitHubApiError) as exc:
                errors.append(f"add_label:{exc}")
        for lbl in stale_status:
            try:
                self.client.remove_label(num, lbl)
                removed.append(lbl)
            except (RateLimitError, PermissionDeniedError, GitHubApiError) as exc:
                errors.append(f"remove_label({lbl}):{exc}")
        return ActionResult(
            issue_number=num,
            decision="confirmed_duplicate",
            canonical_issue=None,
            action_taken="sanitized_closed_labels" if not errors else "partial_auto_close_error",
            dry_run=False,
            labels_added=tuple(added),
            labels_removed=tuple(removed),
            closed=True,
            partial_errors=tuple(errors),
        )

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

        # Ensure any already-closed duplicate issue keeps official Duplicado label and never retains Em análise.
        if live_issue.state == "closed" and (
            live_issue.state_reason == "duplicate"
            or LABEL_DUPLICATE in live_issue.labels
            or (existing_meta and existing_meta.get("action") == "auto_closed")
        ):
            target_label = (
                decision.resolution_label
                if (existing_meta and existing_meta.get("action") == "auto_closed" and decision.match_kind == "already_fulfilled_creation")
                else LABEL_DUPLICATE
            )
            sanitized = self._ensure_closed_duplicate_labels(num, live_issue, target_label)
            if sanitized is not None:
                return ActionResult(
                    issue_number=num,
                    decision=decision.classification,
                    canonical_issue=decision.canonical_issue,
                    action_taken=sanitized.action_taken,
                    dry_run=sanitized.dry_run,
                    comment_id=existing_comment.get("id") if existing_comment else None,
                    labels_added=sanitized.labels_added,
                    labels_removed=sanitized.labels_removed,
                    closed=True,
                    partial_errors=sanitized.partial_errors,
                )

        # Revalidate target & canonical live (catches mid-run edits, deleted canonicals, & author clarifications)
        try:
            decision, live_parsed, canon_parsed, _ = self._revalidate_live_decision(
                decision, live_issue, comments
            )
        except (RateLimitError, PermissionDeniedError, GitHubApiError) as exc:
            return ActionResult(
                issue_number=num,
                decision=decision.classification,
                canonical_issue=decision.canonical_issue,
                action_taken="api_error_skipped",
                dry_run=self.config.dry_run,
                skipped_reason=str(exc),
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
            # Issue was edited or semantic engine refined decision: update existing bot comment cleanly.
            body = render_comment_body(decision, "cleared", self.issues_by_number, repo=self.repo)
            return self._upsert_comment_only(num, decision, "cleared", body, existing_comment)

        target_canon_str = str(decision.canonical_issue) if decision.canonical_issue is not None else "none"
        target_res_label = decision.resolution_label or LABEL_DUPLICATE

        # If this issue was already auto-closed by the guard for the exact same decision/canonical/fp
        # and remains closed with its official resolution label, treat re-runs as pure no-op.
        if (
            existing_meta
            and existing_meta.get("action") == "auto_closed"
            and existing_meta.get("decision") == decision.classification
            and existing_meta.get("canonical") == target_canon_str
            and existing_meta.get("fp") == decision.fingerprint
            and live_issue.state == "closed"
            and (LABEL_DUPLICATE in live_issue.labels or LABEL_DONE in live_issue.labels)
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

        body = render_comment_body(decision, intended_action, self.issues_by_number, repo=self.repo)

        if self.config.dry_run:
            return ActionResult(
                issue_number=num,
                decision=decision.classification,
                canonical_issue=decision.canonical_issue,
                action_taken=f"dry_run_{intended_action}",
                dry_run=True,
                comment_id=existing_comment.get("id") if existing_comment else None,
                labels_added=(target_res_label,) if can_auto_close else (),
                closed=can_auto_close,
                skipped_reason=block_reason,
            )

        if not can_auto_close:
            return self._upsert_comment_only(
                num, decision, "commented", body, existing_comment, skipped_reason=block_reason
            )

        # Perform formal closure transactionally:
        # 1. Verify canonical live (when linking to an open issue) ->
        # 2. Register justification comment with reference & link ->
        # 3. Apply official resolution label (Duplicado or Concluído) ->
        # 4. Close issue (rollback label on failure) ->
        # 5. Re-fetch live issue after close and remove incompatible triage status labels (e.g. Em análise)
        partial_errors: list[str] = []
        labels_added: list[str] = []
        labels_removed: list[str] = []
        comment_id: int | None = existing_comment.get("id") if existing_comment else None
        closed_ok = False

        # 1. Verify canonical issue is STILL open on live API right before mutating anything
        # (For catalog-verified already_fulfilled_creation, canonical_issue may be closed or None.)
        if decision.match_kind != "already_fulfilled_creation" and decision.canonical_issue is not None:
            try:
                live_canon = RawIssue.from_github_dict(self.client.get_issue(decision.canonical_issue))
                if live_canon.state != "open" or LABEL_DUPLICATE in live_canon.labels:
                    fallback_body = render_comment_body(decision, "commented", self.issues_by_number, repo=self.repo)
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

        # 2.5 Re-verify right before adding resolution label (guards edits between comment and label)
        ok_mid, mid_reason, _ = self._verify_concurrent_barrier(
            num,
            live_parsed.content_fingerprint,
            decision,
            canon_parsed,
            comment_id,
        )
        if not ok_mid:
            # Recompute based on latest issue state in API if possible
            try:
                latest_mid = RawIssue.from_github_dict(self.client.get_issue(num))
                downgraded_dec, mode_after = self._downgrade_decision_on_live_abort(decision, latest_mid, mid_reason)
            except (RateLimitError, PermissionDeniedError, GitHubApiError):
                downgraded_dec = replace(decision, classification="possible_duplicate", should_auto_close=False, blocked_reason=mid_reason)
                mode_after = "commented"
            self._downgrade_comment_after_failure(num, comment_id, downgraded_dec, partial_errors, mode=mode_after)
            return ActionResult(
                issue_number=num,
                decision=downgraded_dec.classification,
                canonical_issue=downgraded_dec.canonical_issue,
                action_taken="aborted_concurrent_edit_before_label",
                dry_run=False,
                comment_id=comment_id,
                closed=False,
                skipped_reason=mid_reason,
                partial_errors=tuple(partial_errors),
            )

        # 3. Add official resolution label (Duplicado, or Concluído for standalone catalog fulfillment)
        added_res_now = False
        if target_res_label not in live_issue.labels:
            try:
                self.client.add_labels(num, [target_res_label])
                labels_added.append(target_res_label)
                added_res_now = True
            except (RateLimitError, PermissionDeniedError, GitHubApiError) as exc:
                print(f"[Nox Duplicate Guard] Issue #{num}: falha ao aplicar label {target_res_label}: {exc}", file=sys.stderr)
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

        # 3.5 Pre-close verification barrier (guards edits/comments/canonical changes between label and close)
        ok_pre, pre_reason, _ = self._verify_concurrent_barrier(
            num,
            live_parsed.content_fingerprint,
            decision,
            canon_parsed,
            comment_id,
            allowed_resolution_label=target_res_label,
        )
        if not ok_pre:
            if added_res_now:
                try:
                    self.client.remove_label(num, target_res_label)
                    labels_added.clear()
                except (RateLimitError, PermissionDeniedError, GitHubApiError) as rb_exc:
                    partial_errors.append(f"rollback_label:{rb_exc}")
            try:
                latest_pre = RawIssue.from_github_dict(self.client.get_issue(num))
                downgraded_dec, mode_after = self._downgrade_decision_on_live_abort(decision, latest_pre, pre_reason)
            except (RateLimitError, PermissionDeniedError, GitHubApiError):
                downgraded_dec = replace(decision, classification="possible_duplicate", should_auto_close=False, blocked_reason=pre_reason)
                mode_after = "commented"
            self._downgrade_comment_after_failure(num, comment_id, downgraded_dec, partial_errors, mode=mode_after)
            return ActionResult(
                issue_number=num,
                decision=downgraded_dec.classification,
                canonical_issue=downgraded_dec.canonical_issue,
                action_taken="aborted_concurrent_edit_before_close",
                dry_run=False,
                comment_id=comment_id,
                labels_added=tuple(labels_added),
                closed=False,
                skipped_reason=pre_reason,
                partial_errors=tuple(partial_errors),
            )

        # 4. Close issue with formal state_reason (rollback label if close fails)
        try:
            self.client.close_issue(num, state_reason=decision.close_state_reason or "duplicate")
            closed_ok = True
        except (RateLimitError, PermissionDeniedError, GitHubApiError) as exc:
            print(f"[Nox Duplicate Guard] Issue #{num}: falha ao fechar issue como duplicada: {exc}", file=sys.stderr)
            partial_errors.append(f"close:{exc}")
            if added_res_now:
                try:
                    self.client.remove_label(num, target_res_label)
                    labels_added.clear()
                except (RateLimitError, PermissionDeniedError, GitHubApiError) as rb_exc:
                    print(f"[Nox Duplicate Guard] Issue #{num}: falha ao reverter label {target_res_label}: {rb_exc}", file=sys.stderr)
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

        # 5. Remove incompatible triage status labels ONLY after formal closure succeeds.
        # Re-fetch live issue once to catch any 'Em análise' added concurrently by label-issues.yml.
        live_labels_after_close = set(live_issue.labels)
        try:
            refreshed = RawIssue.from_github_dict(self.client.get_issue(num))
            live_labels_after_close |= set(refreshed.labels)
        except (RateLimitError, PermissionDeniedError, GitHubApiError):
            pass

        for lbl in INCOMPATIBLE_STATUS_LABELS_ON_DUPLICATE:
            if lbl in live_labels_after_close:
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

    def _downgrade_decision_on_live_abort(
        self,
        decision: GuardDecision,
        latest_raw: RawIssue | None,
        abort_reason: str | None = None,
    ) -> tuple[GuardDecision, str]:
        if abort_reason and "catalog_changed_before_close:" in abort_reason:
            if decision.canonical_issue is None:
                return (
                    replace(
                        decision,
                        classification="no_match",
                        should_auto_close=False,
                        blocked_reason=abort_reason,
                    ),
                    "cleared",
                )
            return (
                replace(
                    decision,
                    classification="possible_duplicate",
                    should_auto_close=False,
                    blocked_reason=abort_reason,
                ),
                "commented",
            )
        if latest_raw is not None and self.matcher is not None:
            latest_parsed = parse_issue(latest_raw, known_extensions=self._known_extensions)
            if latest_parsed.content_fingerprint != decision.fingerprint:
                self.issues_by_number[latest_raw.number] = latest_raw
                self.matcher.reindex_issue(latest_parsed)
                re_dec = self.matcher.evaluate(latest_parsed)
                if re_dec.classification == "no_match":
                    return re_dec, "cleared"
                return replace(re_dec, classification="possible_duplicate", should_auto_close=False), "commented"
        return replace(decision, classification="possible_duplicate", should_auto_close=False), "commented"

    def _downgrade_comment_after_failure(
        self,
        issue_number: int,
        comment_id: int | None,
        decision: GuardDecision,
        partial_errors: list[str],
        *,
        mode: str = "commented",
    ) -> None:
        if not comment_id:
            return
        try:
            fallback_body = render_comment_body(decision, mode, self.issues_by_number, repo=self.repo)
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
        if decision.match_kind != "already_fulfilled_creation" and (
            decision.canonical_issue is None or decision.canonical_issue == decision.issue_number
        ):
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
