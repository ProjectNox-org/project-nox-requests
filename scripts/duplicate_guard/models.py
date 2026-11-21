# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Core data models and official Project Nox taxonomy constants."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Classification = Literal["no_match", "possible_duplicate", "confirmed_duplicate"]

CATEGORY_NEW_EXTENSION = "new_extension"
CATEGORY_BUG = "bug"
CATEGORY_DOMAIN_CHANGE = "domain_change"
CATEGORY_DEAD_SOURCE = "dead_source"
CATEGORY_ENHANCEMENT = "enhancement"
CATEGORY_UNKNOWN = "unknown"

ALL_CATEGORIES = frozenset({
    CATEGORY_NEW_EXTENSION,
    CATEGORY_BUG,
    CATEGORY_DOMAIN_CHANGE,
    CATEGORY_DEAD_SOURCE,
    CATEGORY_ENHANCEMENT,
})

OFFICIAL_CATEGORY_LABELS = {
    CATEGORY_NEW_EXTENSION: "Nova extensão",
    CATEGORY_BUG: "Problema na extensão",
    CATEGORY_DOMAIN_CHANGE: "Mudança de domínio",
    CATEGORY_DEAD_SOURCE: "Fonte indisponível",
    CATEGORY_ENHANCEMENT: "Sugestão",
}

OFFICIAL_RESOLUTION_LABELS = frozenset({
    "Concluído",
    "Recusado",
    "Duplicado",
    "Cancelado",
})

OFFICIAL_STATUS_LABELS = frozenset({
    "Em análise",
    "Aceito",
    "Em desenvolvimento",
    "Aguardando informações",
})

HUMAN_INTERVENTION_LABELS = frozenset({
    "Aceito",
    "Em desenvolvimento",
    "Concluído",
    "Recusado",
    "Cancelado",
    "Duplicado",
    "resolution:done",
    "resolution:declined",
    "resolution:duplicate",
    "resolution:cancelled",
    "status:accepted",
    "status:in-progress",
})

INCOMPATIBLE_STATUS_LABELS_ON_DUPLICATE = (
    "Em análise",
    "Aguardando informações",
    "Em desenvolvimento",
    "Aceito",
    "status:triage",
    "status:needs-info",
    "status:in-progress",
    "status:accepted",
)

LABEL_DUPLICATE = "Duplicado"


@dataclass(frozen=True)
class NormalizedUrl:
    """Normalized URL or domain identifier extracted from an issue field."""

    raw: str
    scheme: str
    host: str
    registrable_domain: str
    host_stem: str
    normalized_path: str
    identity_key: str
    is_valid_domain: bool
    is_shared_host: bool
    raw_identifier: str


@dataclass(frozen=True)
class RawIssue:
    """Raw GitHub Issue payload normalized to a compact internal structure."""

    number: int
    title: str
    body: str
    state: str
    state_reason: str | None
    created_at: str
    updated_at: str
    closed_at: str | None
    html_url: str
    labels: tuple[str, ...]
    author: str
    comments_count: int = 0

    @classmethod
    def from_github_dict(cls, data: dict) -> RawIssue:
        """Build a RawIssue from a GitHub REST API issue JSON dictionary."""
        raw_labels = []
        for lbl in data.get("labels") or []:
            if isinstance(lbl, str):
                raw_labels.append(lbl)
            elif isinstance(lbl, dict) and isinstance(lbl.get("name"), str):
                raw_labels.append(lbl["name"])
        author_obj = data.get("user") or data.get("author") or {}
        author_login = str(author_obj.get("login") or "unknown")
        return cls(
            number=int(data["number"]),
            title=str(data.get("title") or ""),
            body=str(data.get("body") or ""),
            state=str(data.get("state") or "open").lower(),
            state_reason=data.get("state_reason"),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or data.get("created_at") or ""),
            closed_at=data.get("closed_at"),
            html_url=str(data.get("html_url") or ""),
            labels=tuple(raw_labels),
            author=author_login,
            comments_count=int(data.get("comments") or 0),
        )


@dataclass(frozen=True)
class ParsedIssue:
    """Structured representation of a Project Nox Request Issue."""

    raw: RawIssue
    category: str
    ext_type: str | None
    name: str
    compact_name: str
    distinctive_tokens: tuple[str, ...]
    clean_title: str
    url: NormalizedUrl | None
    old_url: NormalizedUrl | None
    new_url: NormalizedUrl | None
    extra_urls: tuple[NormalizedUrl, ...]
    version_raw: str
    version_tuple: tuple[int, ...]
    app_name: str
    affected_parts: frozenset[str]
    description: str
    reproduction: str
    additional_info: str
    related_extension: str
    related_extension_compact: str
    error_signatures: frozenset[str]
    technical_tokens: frozenset[str]
    all_text_tokens: frozenset[str]
    resolution: str | None
    active_status: str | None
    is_open: bool
    is_closed: bool
    is_duplicate_marked: bool
    content_fingerprint: str
    body_is_sparse: bool


@dataclass(frozen=True)
class CandidateEvaluation:
    """Result of comparing the target issue against a single older candidate."""

    candidate_number: int
    canonical_number: int
    classification: Classification
    score: float
    reasons: tuple[str, ...]
    contradictions: tuple[str, ...] = ()
    candidate_is_open: bool = True
    candidate_resolution: str | None = None


@dataclass(frozen=True)
class GuardDecision:
    """Final deduplication decision for an issue."""

    issue_number: int
    category: str
    classification: Classification
    canonical_issue: int | None
    candidates: tuple[CandidateEvaluation, ...]
    reasons: tuple[str, ...]
    fingerprint: str
    should_auto_close: bool
    blocked_reason: str | None = None


@dataclass(frozen=True)
class ActionResult:
    """Audit record of GitHub actions taken (or simulated in dry-run) for an issue."""

    issue_number: int
    decision: Classification
    canonical_issue: int | None
    action_taken: str
    dry_run: bool
    comment_id: int | None = None
    labels_added: tuple[str, ...] = ()
    labels_removed: tuple[str, ...] = ()
    closed: bool = False
    skipped_reason: str | None = None
    partial_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ThresholdsConfig:
    """Calibrated category matching thresholds."""

    new_extension_confirmed_name_edit_ratio: float = 0.72
    new_extension_possible_name_edit_ratio: float = 0.88
    bug_confirmed_text_similarity: float = 0.78
    bug_confirmed_structured_text_similarity: float = 0.30
    bug_possible_text_similarity: float = 0.50
    bug_possible_same_part_similarity: float = 0.25
    bug_min_description_chars_for_auto_close: int = 15
    dead_source_confirmed_text_similarity: float = 0.55
    dead_source_possible_text_similarity: float = 0.25
    enhancement_confirmed_combined_similarity: float = 0.85
    enhancement_confirmed_summary_overlap: float = 0.70
    enhancement_possible_tfidf_similarity: float = 0.42
    enhancement_possible_token_overlap: float = 0.30


@dataclass(frozen=True)
class GuardConfig:
    """Runtime configuration for Nox Duplicate Guard."""

    enabled: bool = True
    auto_close_enabled: bool = True
    dry_run: bool = False
    reconcile_lookback_days: int = 30
    max_candidates_in_comment: int = 3
    dead_source_window_days: int = 30
    bug_regression_days: int = 3
    auto_close_categories: frozenset[str] = field(
        default_factory=lambda: frozenset(ALL_CATEGORIES)
    )
    thresholds: ThresholdsConfig = field(default_factory=ThresholdsConfig)
