# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Core data models and official Project Nox taxonomy constants."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal

Classification = Literal["no_match", "possible_duplicate", "confirmed_duplicate"]

RULE_ENGINE_VERSION = "1.5.0"

INTENT_CREATE_EXTENSION = "create_extension"
INTENT_BUG_FIX = "bug_fix"
INTENT_FEATURE_ADDITION = "feature_addition"
INTENT_LANGUAGE_SUPPORT = "language_support"
INTENT_MISSING_CONTENT = "missing_content"
INTENT_LOADING_ISSUE = "loading_issue"
INTENT_DOMAIN_CHANGE = "domain_change"
INTENT_DEAD_SOURCE = "dead_source"

LABEL_DONE = "Concluído"

TRUSTED_BOT_LOGINS = frozenset({
    "github-actions[bot]",
    "github-actions",
})

TRUSTED_AUTHOR_ASSOCIATIONS = frozenset({
    "OWNER",
    "MEMBER",
    "COLLABORATOR",
})

MAINTAINER_OVERRIDE_LABELS = frozenset({
    "ignorar-duplicata",
    "duplicate-guard:ignore",
})

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
    "status:triage",
    "status:needs-info",
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
    destination_key: str = ""
    exact_host: str = ""
    subdomain: str = ""
    port: int | None = None


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
    declared_language: str = ""
    semantic_intent: str = ""
    secondary_intents: frozenset[str] = frozenset()
    inferred_category: str = ""
    category_mismatch: bool = False
    extension_already_exists: bool = False
    mentioned_languages: frozenset[str] = frozenset()
    target_languages: frozenset[str] = frozenset()
    existing_only_languages: frozenset[str] = frozenset()
    feature_topics: frozenset[str] = frozenset()
    symptom_topics: frozenset[str] = frozenset()
    has_substantive_observations: bool = False
    has_regression_signal: bool = False
    specific_work_hint: str = ""
    mentioned_chapters: frozenset[str] = frozenset()
    mentioned_episodes: frozenset[str] = frozenset()
    api_endpoints: frozenset[str] = frozenset()
    title_symptom: str = ""


@dataclass(frozen=True)
class CatalogExtension:
    """Published extension metadata from official Project Nox catalog repositories."""

    ext_type: str
    name: str
    compact_name: str
    pkg: str
    apk: str
    version: str
    version_tuple: tuple[int, ...]
    languages: frozenset[str]
    domains: frozenset[str]
    base_urls: tuple[str, ...]
    catalog_repo: str

    def supports_languages(self, requested: frozenset[str]) -> bool:
        """Return True only when published catalog metadata unambiguously covers every requested language."""
        valid_catalog_langs = {lang for lang in self.languages if not lang.startswith("unknown:")}
        if not valid_catalog_langs:
            return False
        if not requested:
            return False
        if any(lang.startswith("unknown:") for lang in requested):
            return False
        if "all" in requested:
            return "all" in valid_catalog_langs
        if "all" in valid_catalog_langs:
            return True
        return requested <= valid_catalog_langs

    def to_summary_dict(self) -> dict:
        return {
            "ext_type": self.ext_type,
            "name": self.name,
            "pkg": self.pkg,
            "apk": self.apk,
            "version": self.version,
            "languages": sorted(self.languages),
            "base_urls": list(self.base_urls),
            "catalog_repo": self.catalog_repo,
        }


class ExtensionCatalog:
    """Indexed lookup over published extensions in manga-extensoes and anime-extensoes."""

    def __init__(self, extensions: list[CatalogExtension] | tuple[CatalogExtension, ...] = ()) -> None:
        self.extensions: tuple[CatalogExtension, ...] = tuple(extensions)
        self._by_type_domain: dict[tuple[str, str], list[CatalogExtension]] = {}
        self._by_type_name: dict[tuple[str, str], list[CatalogExtension]] = {}
        for ext in self.extensions:
            for dom in ext.domains:
                self._by_type_domain.setdefault((ext.ext_type, dom), []).append(ext)
            if ext.compact_name:
                self._by_type_name.setdefault((ext.ext_type, ext.compact_name), []).append(ext)

    def __bool__(self) -> bool:
        return bool(self.extensions)

    def find_matching_extensions(self, target: ParsedIssue) -> tuple[CatalogExtension, ...]:
        """Return published catalog extensions verified to match target by type + domain + name."""
        if not self.extensions or not target.url or not target.url.is_valid_domain:
            return ()
        types_to_check = (target.ext_type,) if target.ext_type else ("Mangá", "Anime")
        matches: list[CatalogExtension] = []
        seen_pkgs: set[str] = set()
        for ext_type in types_to_check:
            for ext in self._by_type_domain.get((ext_type, target.url.identity_key), ()):
                if ext.pkg in seen_pkgs:
                    continue
                if self._name_compatible(target, ext):
                    seen_pkgs.add(ext.pkg)
                    matches.append(ext)
        return tuple(matches)

    def find_by_name_and_type(self, compact_name: str, ext_type: str | None) -> tuple[CatalogExtension, ...]:
        """Lookup catalog extensions by compact name and optional ext_type."""
        if not self.extensions or not compact_name:
            return ()
        types_to_check = (ext_type,) if ext_type else ("Mangá", "Anime")
        found: list[CatalogExtension] = []
        seen_pkgs: set[str] = set()
        for et in types_to_check:
            for ext in self._by_type_name.get((et, compact_name), ()):
                if ext.pkg not in seen_pkgs:
                    seen_pkgs.add(ext.pkg)
                    found.append(ext)
        return tuple(found)

    def digest(self) -> str:
        """Return a stable 16-char digest of catalog entries for cache invalidation."""
        if not self.extensions:
            return "empty"
        rows = sorted(
            (
                ext.catalog_repo,
                ext.ext_type,
                ext.pkg,
                ext.version,
                ",".join(sorted(ext.languages)),
                ",".join(sorted(ext.domains)),
            )
            for ext in self.extensions
        )
        raw = "|".join(":".join(r) for r in rows)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _name_compatible(target: ParsedIssue, ext: CatalogExtension) -> bool:
        from .normalize import levenshtein_ratio

        t_name = target.compact_name
        e_name = ext.compact_name
        if not t_name or not e_name:
            return False
        if t_name == e_name or t_name in e_name or e_name in t_name:
            return True
        if target.url and target.url.host_stem and len(target.url.host_stem) >= 5:
            if target.url.host_stem in e_name and target.url.host_stem in t_name:
                if levenshtein_ratio(t_name, e_name) >= 0.72:
                    return True
        return levenshtein_ratio(t_name, e_name) >= 0.80


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
    match_kind: str = "duplicate"
    intent_relation: str = "same"


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
    match_kind: str = "none"
    semantic_intent: str = ""
    inferred_category: str = ""
    category_mismatch: bool = False
    catalog_extension: dict | None = None
    resolution_label: str = LABEL_DUPLICATE
    close_state_reason: str = "duplicate"


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
    batch_size: int = 50
    max_candidates_in_comment: int = 3
    dead_source_window_days: int = 30
    bug_regression_days: int = 3
    auto_close_categories: frozenset[str] = field(
        default_factory=lambda: frozenset(ALL_CATEGORIES)
    )
    thresholds: ThresholdsConfig = field(default_factory=ThresholdsConfig)
