# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Domain-specific duplicate detection and decision engine for all 5 ticket categories."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .models import (
    ALL_CATEGORIES,
    CATEGORY_BUG,
    CATEGORY_DEAD_SOURCE,
    CATEGORY_DOMAIN_CHANGE,
    CATEGORY_ENHANCEMENT,
    CATEGORY_NEW_EXTENSION,
    CandidateEvaluation,
    GuardConfig,
    GuardDecision,
    ParsedIssue,
)
from .normalize import (
    build_corpus_idf,
    classify_dead_source_evidence,
    dice_bigrams,
    jaccard_similarity,
    levenshtein_ratio,
    name_without_tld,
    normalize_text,
    overlap_coefficient,
    tfidf_cosine,
    tokenize_list,
)

_PART_INCOMPATIBLE_GROUPS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (frozenset({"login"}), frozenset({"reader", "video", "search", "catalog", "chapters", "metadata", "install"})),
    (frozenset({"search"}), frozenset({"reader", "video", "login", "install"})),
    (frozenset({"install"}), frozenset({"reader", "video", "search", "chapters", "metadata", "login"})),
    (frozenset({"reader"}), frozenset({"search", "login", "install"})),
    (frozenset({"video"}), frozenset({"search", "login", "install"})),
)

_ADJACENT_PARTS: tuple[frozenset[str], ...] = (
    frozenset({"chapters", "reader"}),
    frozenset({"chapters", "video"}),
    frozenset({"catalog", "metadata"}),
)

_SIG_INCOMPATIBLE_PAIRS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (frozenset({"login_auth"}), frozenset({"no_pages_found", "video_playback", "slow_download", "missing_chapters"})),
    (frozenset({"slow_download"}), frozenset({"json_decode", "nxdomain_dns", "login_auth", "no_pages_found"})),
    (frozenset({"video_playback"}), frozenset({"login_auth"})),
)

_CANONICAL_REF_PATTERNS = (
    re.compile(r"<!--\s*nox-duplicate-guard:v1\b[^>]*\bcanonical=(\d+)\b", re.IGNORECASE),
    re.compile(r"(?:duplicad[oa]\s+(?:do|da|de)\s+(?:issue|ticket)\s*#?(\d+)|duplicate\s+of\s+#(\d+))", re.IGNORECASE),
)


def _parse_iso_dt(raw_ts: str | None) -> datetime:
    if not raw_ts:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        return datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def _issue_order_key(issue: ParsedIssue) -> tuple[datetime, int]:
    return (_parse_iso_dt(issue.raw.created_at), issue.raw.number)


def _days_between(ts_a: str | None, ts_b: str | None) -> float:
    dt_a = _parse_iso_dt(ts_a)
    dt_b = _parse_iso_dt(ts_b)
    return abs((dt_a - dt_b).total_seconds()) / 86400.0


def extract_canonical_reference(comments: list[dict] | list[str]) -> int | None:
    """Extract canonical issue number referenced in existing bot/human comments."""
    for entry in comments:
        body = entry.get("body", "") if isinstance(entry, dict) else str(entry or "")
        for pat in _CANONICAL_REF_PATTERNS:
            m = pat.search(body)
            if m:
                num_str = next((g for g in m.groups() if g), None)
                if num_str and num_str.isdigit():
                    return int(num_str)
    return None


def _same_extension_name(a: str, b: str, *, allow_fuzzy: bool = True, threshold: float = 0.86) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    na = name_without_tld(a)
    nb = name_without_tld(b)
    if na and nb and na == nb:
        return True
    if not allow_fuzzy:
        return False
    return levenshtein_ratio(na or a, nb or b) >= threshold


def _same_parsed_extension_name(
    target: ParsedIssue,
    cand: ParsedIssue,
    *,
    fuzzy_threshold: float = 0.86,
) -> bool:
    if target.compact_name and cand.compact_name and target.compact_name == cand.compact_name:
        return True
    nt = name_without_tld(target.name)
    nc = name_without_tld(cand.name)
    if nt and nc and nt == nc:
        return True
    dt_joined = "".join(target.distinctive_tokens)
    dc_joined = "".join(cand.distinctive_tokens)
    if not dt_joined or not dc_joined:
        return False
    if dt_joined == dc_joined:
        return True
    # Only allow fuzzy match when distinctive stems have >= 5 chars to avoid short collisions (e.g. ani/ana)
    if min(len(dt_joined), len(dc_joined)) >= 5:
        return levenshtein_ratio(dt_joined, dc_joined) >= fuzzy_threshold
    return False


def _types_contradict(a: ParsedIssue, b: ParsedIssue) -> bool:
    return bool(a.ext_type and b.ext_type and a.ext_type != b.ext_type)


def _combined_text_tokens(issue: ParsedIssue) -> list[str]:
    text = f"{issue.clean_title} {issue.description} {issue.reproduction} {issue.additional_info}"
    return tokenize_list(text)


def _parts_status(parts_a: frozenset[str], parts_b: frozenset[str]) -> str:
    """Return 'same', 'adjacent', 'contradictory', or 'neutral'."""
    concrete_a = parts_a - {"other"}
    concrete_b = parts_b - {"other"}
    if not concrete_a or not concrete_b:
        return "neutral"
    if concrete_a & concrete_b:
        return "same"
    for g1, g2 in _PART_INCOMPATIBLE_GROUPS:
        if (concrete_a <= g1 and concrete_b <= g2) or (concrete_b <= g1 and concrete_a <= g2):
            return "contradictory"
    union = concrete_a | concrete_b
    for adj in _ADJACENT_PARTS:
        if union <= adj:
            return "adjacent"
    return "neutral"


def _signatures_contradict(sigs_a: frozenset[str], sigs_b: frozenset[str]) -> bool:
    http_a = {s for s in sigs_a if s.startswith("http:")}
    http_b = {s for s in sigs_b if s.startswith("http:")}
    if http_a and http_b and not (http_a & http_b):
        return True
    for g1, g2 in _SIG_INCOMPATIBLE_PAIRS:
        if (sigs_a & g1 and sigs_b & g2) or (sigs_b & g1 and sigs_a & g2):
            return True
    return False


class DuplicateMatcher:
    """Deterministic hybrid duplicate matcher for Project Nox Requests."""

    def __init__(
        self,
        issues: list[ParsedIssue],
        config: GuardConfig,
        duplicate_links: dict[int, int] | None = None,
    ) -> None:
        self.config = config
        self.by_number: dict[int, ParsedIssue] = {p.raw.number: p for p in issues}
        self.duplicate_links: dict[int, int] = dict(duplicate_links or {})
        self.by_category: dict[str, list[ParsedIssue]] = {}
        self._tokens_cache: dict[int, list[str]] = {}
        self._idf_by_category: dict[str, dict[str, float]] = {}

        for p in issues:
            self.by_category.setdefault(p.category, []).append(p)
            self._tokens_cache[p.raw.number] = _combined_text_tokens(p)

        for cat, cat_issues in self.by_category.items():
            cat_issues.sort(key=_issue_order_key)
            self._idf_by_category[cat] = build_corpus_idf(
                [self._tokens_cache[i.raw.number] for i in cat_issues]
            )

    def resolve_canonical(self, candidate_num: int) -> int:
        """Follow duplicate chains deterministically without cycles to a valid canonical issue."""
        visited: set[int] = set()
        curr = candidate_num
        while curr not in visited:
            visited.add(curr)
            issue = self.by_number.get(curr)
            if not issue or not issue.is_duplicate_marked:
                return curr
            linked = self.duplicate_links.get(curr)
            if linked and linked not in visited and linked in self.by_number:
                linked_issue = self.by_number[linked]
                if _issue_order_key(linked_issue) < _issue_order_key(issue):
                    curr = linked
                    continue
            # Fallback: find earliest non-duplicate issue in same category matching best
            fallback = self._find_fallback_canonical(issue, visited)
            if fallback is not None:
                return fallback
            return curr
        return candidate_num

    def _find_fallback_canonical(self, dup_issue: ParsedIssue, visited: set[int]) -> int | None:
        best_eval: CandidateEvaluation | None = None
        for cand in self.by_category.get(dup_issue.category, ()):
            if cand.raw.number in visited:
                continue
            if _issue_order_key(cand) >= _issue_order_key(dup_issue):
                continue
            if cand.is_duplicate_marked:
                continue
            ev = self._evaluate_pair(dup_issue, cand, resolve_chain=False)
            if ev and ev.classification in ("confirmed_duplicate", "possible_duplicate"):
                if best_eval is None or self._rank_key(ev) > self._rank_key(best_eval):
                    best_eval = ev
        return best_eval.candidate_number if best_eval else None

    @staticmethod
    def _rank_key(ev: CandidateEvaluation) -> tuple[int, int, float, int]:
        cls_rank = {"confirmed_duplicate": 2, "possible_duplicate": 1, "no_match": 0}[ev.classification]
        open_rank = 1 if ev.candidate_is_open else 0
        # Prefer confirmed -> open -> higher score -> older/smaller issue number
        return (cls_rank, open_rank, round(ev.score, 4), -ev.canonical_number)

    def evaluate(self, target: ParsedIssue) -> GuardDecision:
        """Evaluate target issue against all strictly older candidates in the same category."""
        if not self.config.enabled:
            return GuardDecision(
                issue_number=target.raw.number,
                category=target.category,
                classification="no_match",
                canonical_issue=None,
                candidates=(),
                reasons=("Nox Duplicate Guard desativado na configuração.",),
                fingerprint=target.content_fingerprint,
                should_auto_close=False,
                blocked_reason="disabled",
            )

        if target.category not in ALL_CATEGORIES:
            return GuardDecision(
                issue_number=target.raw.number,
                category=target.category,
                classification="no_match",
                canonical_issue=None,
                candidates=(),
                reasons=("Categoria não identificada com segurança; processamento conservador.",),
                fingerprint=target.content_fingerprint,
                should_auto_close=False,
                blocked_reason="unknown_category",
            )

        target_key = _issue_order_key(target)
        if target.raw.number not in self._tokens_cache:
            self._tokens_cache[target.raw.number] = _combined_text_tokens(target)

        matches: list[CandidateEvaluation] = []
        for cand in self.by_category.get(target.category, ()):
            if cand.raw.number == target.raw.number:
                continue
            if _issue_order_key(cand) >= target_key:
                continue
            ev = self._evaluate_pair(target, cand, resolve_chain=True)
            if ev is not None and ev.classification != "no_match":
                matches.append(ev)

        if not matches:
            return GuardDecision(
                issue_number=target.raw.number,
                category=target.category,
                classification="no_match",
                canonical_issue=None,
                candidates=(),
                reasons=("Nenhuma solicitação equivalente ou semelhante encontrada.",),
                fingerprint=target.content_fingerprint,
                should_auto_close=False,
            )

        # Deduplicate matches pointing to the same canonical issue, keeping strongest evaluation
        by_canonical: dict[int, CandidateEvaluation] = {}
        for ev in matches:
            prev = by_canonical.get(ev.canonical_number)
            if prev is None or self._rank_key(ev) > self._rank_key(prev):
                by_canonical[ev.canonical_number] = ev

        ranked = sorted(by_canonical.values(), key=self._rank_key, reverse=True)
        top = ranked[0]

        # Canonical validity safety checks before allowing auto-close
        canonical_obj = self.by_number.get(top.canonical_number)
        canonical_is_valid_open = bool(
            canonical_obj
            and canonical_obj.is_open
            and not canonical_obj.is_duplicate_marked
            and _issue_order_key(canonical_obj) < target_key
        )

        classification = top.classification
        blocked_reason: str | None = None
        should_auto_close = False

        if classification == "confirmed_duplicate":
            if not canonical_is_valid_open:
                classification = "possible_duplicate"
                blocked_reason = "canonical_not_open_or_invalid"
            elif not self.config.auto_close_enabled:
                blocked_reason = "auto_close_disabled"
            elif target.category not in self.config.auto_close_categories:
                blocked_reason = "category_auto_close_disabled"
            elif target.is_closed:
                blocked_reason = "target_already_closed"
            else:
                should_auto_close = True

        top_candidates = tuple(ranked[: self.config.max_candidates_in_comment])
        return GuardDecision(
            issue_number=target.raw.number,
            category=target.category,
            classification=classification,
            canonical_issue=top.canonical_number,
            candidates=top_candidates,
            reasons=top.reasons,
            fingerprint=target.content_fingerprint,
            should_auto_close=should_auto_close,
            blocked_reason=blocked_reason,
        )

    def _evaluate_pair(
        self,
        target: ParsedIssue,
        cand: ParsedIssue,
        *,
        resolve_chain: bool,
    ) -> CandidateEvaluation | None:
        if _types_contradict(target, cand):
            return None

        if target.category == CATEGORY_NEW_EXTENSION:
            res = self._match_new_extension(target, cand)
        elif target.category == CATEGORY_BUG:
            res = self._match_bug(target, cand)
        elif target.category == CATEGORY_DOMAIN_CHANGE:
            res = self._match_domain_change(target, cand)
        elif target.category == CATEGORY_DEAD_SOURCE:
            res = self._match_dead_source(target, cand)
        elif target.category == CATEGORY_ENHANCEMENT:
            res = self._match_enhancement(target, cand)
        else:
            return None

        if res is None:
            return None

        classification, score, reasons, contradictions = res
        canonical_num = self.resolve_canonical(cand.raw.number) if resolve_chain else cand.raw.number
        canon_obj = self.by_number.get(canonical_num, cand)

        # If target points back to itself or canonical is somehow newer, reject safely
        if canonical_num == target.raw.number or _issue_order_key(canon_obj) >= _issue_order_key(target):
            return None

        # Never auto-close when canonical resolved to a declined/cancelled/closed issue
        if classification == "confirmed_duplicate" and (not canon_obj.is_open or canon_obj.resolution in ("declined", "cancelled", "done")):
            classification = "possible_duplicate"
            reasons = (*reasons, "O ticket original encontra-se encerrado; marcada revisão humana.")

        return CandidateEvaluation(
            candidate_number=cand.raw.number,
            canonical_number=canonical_num,
            classification=classification,
            score=score,
            reasons=reasons,
            contradictions=contradictions,
            candidate_is_open=canon_obj.is_open,
            candidate_resolution=canon_obj.resolution,
        )

    def _match_new_extension(self, target: ParsedIssue, cand: ParsedIssue):
        t_url, c_url = target.url, cand.url
        t_name, c_name = target.compact_name, cand.compact_name
        th = self.config.thresholds

        both_valid_urls = bool(t_url and t_url.is_valid_domain and c_url and c_url.is_valid_domain)
        same_domain = both_valid_urls and (t_url.identity_key == c_url.identity_key)

        name_exact = bool(t_name and c_name and (t_name == c_name or name_without_tld(target.name) == name_without_tld(cand.name)))
        name_ratio = levenshtein_ratio(t_name, c_name) if (t_name and c_name) else 0.0
        dt_t = "".join(target.distinctive_tokens)
        dt_c = "".join(cand.distinctive_tokens)
        dist_ratio = levenshtein_ratio(dt_t, dt_c) if (dt_t and dt_c) else 0.0

        stem_compat = False
        if same_domain and t_url.host_stem:
            stem_compat = (
                levenshtein_ratio(t_name, t_url.host_stem) >= 0.65
                and levenshtein_ratio(c_name, t_url.host_stem) >= 0.65
            ) or (t_url.host_stem in t_name and t_url.host_stem in c_name)

        if both_valid_urls and not same_domain:
            # Different validated domains NEVER auto-close as confirmed duplicate
            if name_exact and target.ext_type == cand.ext_type and target.ext_type is not None:
                return (
                    "possible_duplicate",
                    0.62,
                    ("Mesmo nome e tipo, porém domínios distintos; requer verificação manual.",),
                    ("different_domains",),
                )
            if (
                dt_t and dt_c and min(len(dt_t), len(dt_c)) >= 5
                and dist_ratio >= th.new_extension_possible_name_edit_ratio
                and target.ext_type == cand.ext_type
            ):
                return (
                    "possible_duplicate",
                    0.55,
                    ("Nomes quase idênticos, porém domínios distintos; requer verificação manual.",),
                    ("different_domains",),
                )
            return None

        if same_domain:
            same_known_type = bool(target.ext_type and cand.ext_type and target.ext_type == cand.ext_type)
            name_compatible = (
                name_exact
                or name_ratio >= th.new_extension_confirmed_name_edit_ratio
                or stem_compat
            )
            if same_known_type and name_compatible:
                if cand.is_open:
                    return (
                        "confirmed_duplicate",
                        0.98 if name_exact else 0.93,
                        (
                            f"Mesmo tipo ({target.ext_type}), mesmo domínio normalizado ({t_url.identity_key.split(':', 1)[-1]}) e nome equivalente.",
                        ),
                        (),
                    )
                return (
                    "possible_duplicate",
                    0.82,
                    (
                        f"Mesmo tipo ({target.ext_type}) e domínio ({t_url.identity_key.split(':', 1)[-1]}) encontrados em solicitação já encerrada.",
                    ),
                    (),
                )
            if same_known_type:
                return (
                    "possible_duplicate",
                    0.75,
                    (f"Mesmo domínio normalizado ({t_url.identity_key.split(':', 1)[-1]}) com nome divergente.",),
                    ("name_divergence",),
                )
            if not target.ext_type or not cand.ext_type:
                return (
                    "possible_duplicate",
                    0.70,
                    ("Mesmo domínio normalizado; tipo Mangá/Anime ausente em um dos tickets.",),
                    (),
                )

        # Missing URL on at least one side: never confirmed automatically
        if name_exact and len(t_name) >= 4 and cand.is_open:
            return (
                "possible_duplicate",
                0.60,
                ("Mesmo nome da fonte, mas sem URL validada em ambos os tickets para confirmação automática.",),
                ("missing_url",),
            )
        return None

    def _match_bug(self, target: ParsedIssue, cand: ParsedIssue):
        if not _same_parsed_extension_name(target, cand):
            return None

        th = self.config.thresholds
        parts_rel = _parts_status(target.affected_parts, cand.affected_parts)
        shared_sigs = target.error_signatures & cand.error_signatures
        shared_tech = target.technical_tokens & cand.technical_tokens

        if _signatures_contradict(target.error_signatures, cand.error_signatures):
            return None

        if parts_rel == "contradictory" and not shared_tech:
            return None

        # Regression protection against closed issues
        if cand.is_closed:
            if cand.resolution in ("declined", "cancelled"):
                return None
            if target.version_tuple and cand.version_tuple and target.version_tuple > cand.version_tuple:
                return None
            ref_closed = cand.raw.closed_at or cand.raw.created_at
            if _days_between(target.raw.created_at, ref_closed) >= self.config.bug_regression_days:
                return None

        tok_a = self._tokens_cache.get(target.raw.number) or _combined_text_tokens(target)
        tok_b = self._tokens_cache.get(cand.raw.number) or _combined_text_tokens(cand)
        idf = self._idf_by_category.get(CATEGORY_BUG)
        cos_sim = tfidf_cosine(tok_a, tok_b, idf)
        jac_sim = jaccard_similarity(target.all_text_tokens, cand.all_text_tokens)
        dice_sim = dice_bigrams(
            f"{target.clean_title} {target.description}",
            f"{cand.clean_title} {cand.description}",
        )
        text_sim = 0.55 * cos_sim + 0.30 * jac_sim + 0.15 * dice_sim

        # Insufficient information protection
        if target.body_is_sparse or cand.body_is_sparse:
            if cand.is_open and parts_rel == "same" and text_sim >= 0.35:
                return (
                    "possible_duplicate",
                    round(min(0.65, text_sim + 0.2), 3),
                    ("Mesma extensão e parte afetada, mas descrição insuficiente para encerramento automático.",),
                    ("insufficient_details",),
                )
            return None

        # Never auto-close against closed bugs even if < bug_regression_days
        if cand.is_closed:
            if parts_rel in ("same", "adjacent") and (shared_sigs or text_sim >= 0.55):
                return (
                    "possible_duplicate",
                    round(min(0.75, text_sim + 0.15), 3),
                    ("Mesma extensão e sintoma semelhante a um ticket recém-fechado; verificar possível regressão.",),
                    ("candidate_closed",),
                )
            return None

        # Open candidate: check confirmed criteria
        has_defined_part_overlap = parts_rel in ("same", "adjacent")
        both_have_specific_sig = bool(
            shared_sigs & {"no_pages_found", "no_results_found", "nxdomain_dns", "json_decode", "null_pointer"}
        )

        # Path 1: Nearly identical description + compatible parts
        if parts_rel in ("same", "adjacent", "neutral") and text_sim >= th.bug_confirmed_text_similarity:
            return (
                "confirmed_duplicate",
                round(min(0.99, text_sim + 0.1), 3),
                (
                    f"Mesma extensão ({target.name}), parte afetada compatível e descrição equivalente do defeito.",
                ),
                (),
            )

        # Path 2: Specific structured error evidence + compatible/adjacent affected part
        if shared_sigs and (has_defined_part_overlap or (parts_rel == "neutral" and both_have_specific_sig)):
            # Do not auto-confirm generic 'cloudflare' without part overlap and strong text match
            only_generic_sig = shared_sigs <= {"cloudflare", "timeout", "site_blocked"}
            if not only_generic_sig or (parts_rel == "same" and text_sim >= th.bug_confirmed_structured_text_similarity):
                if (
                    text_sim >= th.bug_confirmed_structured_text_similarity
                    or shared_tech
                    or ("no_pages_found" in shared_sigs and parts_rel in ("same", "adjacent"))
                    or ("nxdomain_dns" in shared_sigs)
                ):
                    sigs_str = ", ".join(sorted(shared_sigs))
                    return (
                        "confirmed_duplicate",
                        round(min(0.97, max(0.86, text_sim + 0.35)), 3),
                        (
                            f"Mesma extensão ({target.name}), parte afetada compatível e evidência de erro idêntica ({sigs_str}).",
                        ),
                        (),
                    )

        # Possible duplicate alerts (human review only)
        if parts_rel == "same" and text_sim >= th.bug_possible_same_part_similarity:
            return (
                "possible_duplicate",
                round(min(0.79, text_sim + 0.25), 3),
                (f"Mesma extensão ({target.name}) e mesma parte afetada com descrição semelhante.",),
                (),
            )
        if shared_sigs and parts_rel != "contradictory":
            sigs_str = ", ".join(sorted(shared_sigs))
            return (
                "possible_duplicate",
                round(min(0.75, max(0.55, text_sim + 0.25)), 3),
                (f"Mesma extensão ({target.name}) com sintoma relacionado ({sigs_str}), porém parte/contexto distinto.",),
                (),
            )
        if text_sim >= th.bug_possible_text_similarity and parts_rel != "contradictory":
            return (
                "possible_duplicate",
                round(min(0.74, text_sim), 3),
                (f"Mesma extensão ({target.name}) com alta similaridade textual no relato.",),
                (),
            )
        return None

    def _match_domain_change(self, target: ParsedIssue, cand: ParsedIssue):
        if not _same_parsed_extension_name(target, cand):
            return None

        t_old, t_new = target.old_url, target.new_url
        c_old, c_new = cand.old_url, cand.new_url

        if not t_new or not c_new:
            return None

        # Different validated destination domains => distinct migration (e.g. #130 vs #153)
        if t_new.is_valid_domain and c_new.is_valid_domain:
            if t_new.identity_key != c_new.identity_key:
                return None
            same_new = True
        else:
            same_new = t_new.identity_key == c_new.identity_key

        if not same_new:
            return None

        # Both target and candidate share the SAME destination domain
        same_old_valid = bool(
            t_old and c_old and t_old.is_valid_domain and c_old.is_valid_domain
            and t_old.identity_key == c_old.identity_key
        )
        old_contradicts = bool(
            t_old and c_old and t_old.is_valid_domain and c_old.is_valid_domain
            and t_old.identity_key != c_old.identity_key
        )
        if old_contradicts:
            return (
                "possible_duplicate",
                0.64,
                ("Mesma URL nova informada, mas URLs antigas divergem; requer revisão humana.",),
                ("old_url_mismatch",),
            )

        if same_old_valid and t_new.is_valid_domain:
            if cand.is_open:
                return (
                    "confirmed_duplicate",
                    0.99,
                    (
                        f"Mesma extensão ({target.name}), mesma URL antiga ({t_old.host}) e mesma URL nova ({t_new.host}).",
                    ),
                    (),
                )
            return (
                "possible_duplicate",
                0.80,
                (f"Transição idêntica ({t_old.host} → {t_new.host}) já registrada em ticket encerrado.",),
                ("candidate_closed",),
            )

        return (
            "possible_duplicate",
            0.72,
            ("Mesma URL nova informada para a extensão, sem confirmação completa de URL antiga válida.",),
            (),
        )

    def _match_dead_source(self, target: ParsedIssue, cand: ParsedIssue):
        if not _same_parsed_extension_name(target, cand):
            return None

        if _days_between(target.raw.created_at, cand.raw.created_at) > self.config.dead_source_window_days:
            return None

        # Compare domains if both explicitly provide URLs
        t_url = target.url or (target.extra_urls[0] if target.extra_urls else None)
        c_url = cand.url or (cand.extra_urls[0] if cand.extra_urls else None)
        if t_url and c_url and t_url.is_valid_domain and c_url.is_valid_domain:
            if t_url.identity_key != c_url.identity_key:
                return None

        ev_t = classify_dead_source_evidence(f"{target.description} {target.additional_info}")
        ev_c = classify_dead_source_evidence(f"{cand.description} {cand.additional_info}")

        tok_a = self._tokens_cache.get(target.raw.number) or _combined_text_tokens(target)
        tok_b = self._tokens_cache.get(cand.raw.number) or _combined_text_tokens(cand)
        cos_sim = tfidf_cosine(tok_a, tok_b, self._idf_by_category.get(CATEGORY_DEAD_SOURCE))
        jac_sim = jaccard_similarity(target.all_text_tokens, cand.all_text_tokens)
        text_sim = 0.65 * cos_sim + 0.35 * jac_sim
        th = self.config.thresholds

        if "temporary" in (ev_t, ev_c):
            if ev_t != ev_c:
                return (
                    "possible_duplicate",
                    0.50,
                    ("Mesma fonte citada, porém um relato sugere instabilidade temporária enquanto outro cita encerramento.",),
                    ("temporary_vs_permanent",),
                )
            return None

        if cand.is_open and ev_t == "permanent" and ev_c == "permanent" and (
            text_sim >= th.dead_source_confirmed_text_similarity
            or (t_url and c_url and t_url.identity_key == c_url.identity_key and text_sim >= 0.35)
        ):
            return (
                "confirmed_duplicate",
                round(min(0.95, text_sim + 0.25), 3),
                (f"Mesma fonte ({target.name}) reportada como encerrada definitivamente no mesmo período.",),
                (),
            )

        if text_sim >= th.dead_source_possible_text_similarity or (ev_t == "permanent" and ev_c == "permanent"):
            return (
                "possible_duplicate",
                round(min(0.75, max(0.52, text_sim + 0.2)), 3),
                (f"Mesma fonte ({target.name}) já reportada como indisponível recentemente; confirmar se trata do mesmo evento.",),
                (),
            )
        return None

    def _match_enhancement(self, target: ParsedIssue, cand: ParsedIssue):
        ext_t = target.related_extension_compact
        ext_c = cand.related_extension_compact

        # Distinct named extensions (e.g. #144 Blackout Comics vs #151 Hanami Heaven) => NO_MATCH
        if ext_t and ext_c:
            if not _same_extension_name(ext_t, ext_c, allow_fuzzy=True, threshold=0.88):
                return None
        elif bool(ext_t) != bool(ext_c):
            # One targets a specific extension while the other is global
            return None

        tok_a = self._tokens_cache.get(target.raw.number) or _combined_text_tokens(target)
        tok_b = self._tokens_cache.get(cand.raw.number) or _combined_text_tokens(cand)
        cos_sim = tfidf_cosine(tok_a, tok_b, self._idf_by_category.get(CATEGORY_ENHANCEMENT))
        jac_sim = jaccard_similarity(target.all_text_tokens, cand.all_text_tokens)
        title_sim = dice_bigrams(target.clean_title, cand.clean_title)
        combined = 0.60 * cos_sim + 0.25 * jac_sim + 0.15 * title_sim
        tok_overlap = overlap_coefficient(target.all_text_tokens, cand.all_text_tokens)
        th = self.config.thresholds

        if (
            cand.is_open
            and combined >= th.enhancement_confirmed_combined_similarity
            and tok_overlap >= th.enhancement_confirmed_summary_overlap
        ):
            return (
                "confirmed_duplicate",
                round(min(0.98, combined), 3),
                ("Sugestão de melhoria funcionalmente idêntica no mesmo escopo.",),
                (),
            )

        if cand.is_open and (
            (cos_sim >= th.enhancement_possible_tfidf_similarity or combined >= 0.48)
            and tok_overlap >= th.enhancement_possible_token_overlap
        ):
            return (
                "possible_duplicate",
                round(min(0.78, combined + 0.12), 3),
                ("Sugestão semelhante no mesmo escopo; priorizada revisão humana antes de encerrar.",),
                (),
            )

        if cand.is_closed and combined >= 0.65 and tok_overlap >= 0.50:
            return (
                "possible_duplicate",
                round(min(0.70, combined), 3),
                ("Sugestão semelhante a uma solicitação já encerrada.",),
                ("candidate_closed",),
            )
        return None
