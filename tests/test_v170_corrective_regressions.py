# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Corrective regression suite for Nox Duplicate Guard v1.7.0 audit findings."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.duplicate_guard.actions import GuardActionExecutor  # noqa: E402
from scripts.duplicate_guard.cli import parse_corpus, run_cli  # noqa: E402
from scripts.duplicate_guard.config import load_config  # noqa: E402
from scripts.duplicate_guard.matcher import DuplicateMatcher  # noqa: E402
from scripts.duplicate_guard.models import LABEL_DUPLICATE, RULE_ENGINE_VERSION  # noqa: E402
from scripts.duplicate_guard.normalize import normalize_url  # noqa: E402
from scripts.duplicate_guard.parser import parse_issue  # noqa: E402
from tests.test_duplicate_guard import InMemoryGitHubApi, make_raw  # noqa: E402


def _cfg():
    return load_config(REPO_ROOT / ".github" / "duplicate-guard.json")


def _ne(
    num: int,
    name: str,
    url: str,
    *,
    ext_type: str = "Mangá",
    lang: str = "Português",
    notes: str = "_No response_",
    login: str = "Não",
    vip: str = "Não",
    nsfw: str = "Não",
    created_at: str = "2026-11-01T10:00:00Z",
    labels: list[str] | None = None,
):
    lbls = labels or ["Nova extensão", ext_type, "Em análise"]
    body = (
        f"### Nome da extensão\n{name}\n"
        f"### Tipo\n{ext_type}\n"
        f"### Idioma\n{lang}\n"
        f"### URL do site\n{url}\n"
        f"### Precisa de login?\n{login}\n"
        f"### Precisa de VIP/Premium?\n{vip}\n"
        f"### Conteúdo +18?\n{nsfw}\n"
        f"### Observações\n{notes}\n"
    )
    return make_raw(
        num,
        f"[Nova extensão]: {name}",
        body,
        labels=lbls,
        created_at=created_at,
        author=f"user{num}",
    )


def _eval(issues, target_num):
    parsed = parse_corpus(issues)
    target = next(p for p in parsed if p.raw.number == target_num)
    matcher = DuplicateMatcher(parsed, _cfg())
    return matcher.evaluate(target), target, parsed


# ---------------------------------------------------------------------------
# Item 1: Different substantive observations with empty feature_topics
# ---------------------------------------------------------------------------

def test_v170_sec1_divergent_substantive_observations_with_empty_topics_preserved_open():
    i1 = _ne(
        7101,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso que os capítulos sejam ordenados pela data de publicação.",
    )
    i2 = _ne(
        7102,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso que os capítulos sejam agrupados pelo nome do autor.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, p2, parsed = _eval([i1, i2], 7102)
    assert parsed[0].feature_topics == frozenset()
    assert p2.feature_topics == frozenset()
    assert parsed[0].has_substantive_observations is True
    assert p2.has_substantive_observations is True
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False
    assert "scope_or_observation_divergence" in dec.candidates[0].contradictions


def test_v170_sec1_divergent_substantive_observations_without_intro_verb():
    i1 = _ne(
        7103,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Capítulos ordenados pela data de publicação.",
    )
    i2 = _ne(
        7104,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Capítulos agrupados pelo nome do autor.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, _, parsed = _eval([i1, i2], 7104)
    assert parsed[0].has_substantive_observations is True
    assert parsed[1].has_substantive_observations is True
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


# ---------------------------------------------------------------------------
# Item 2: Same generic feature_topic with different observation requirements
# ---------------------------------------------------------------------------

def test_v170_sec1_same_generic_feature_topic_with_different_requirements_preserved_open():
    i1 = _ne(
        7105,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso de filtro por ano de lançamento.",
    )
    i2 = _ne(
        7106,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso de filtro por gênero e autor.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, p2, parsed = _eval([i1, i2], 7106)
    assert parsed[0].feature_topics == p2.feature_topics == frozenset({"search_filter"})
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


# ---------------------------------------------------------------------------
# Item 3: Semantically equivalent substantive observations phrased differently
# ---------------------------------------------------------------------------

def test_v170_sec1_equivalent_substantive_observations_differently_phrased_auto_closes():
    i1 = _ne(
        7107,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso que os capítulos sejam ordenados pela data de publicação.",
    )
    i2 = _ne(
        7108,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Gostaria que a lista de capítulos fosse ordenada por data de lançamento.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, _, _ = _eval([i1, i2], 7108)
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True


# ---------------------------------------------------------------------------
# Item 4: Generic praise vs concrete requirement, and praise not blocking true dup
# ---------------------------------------------------------------------------

def test_v170_sec1_generic_praise_vs_concrete_requirement_and_praise_equivalence():
    i_req = _ne(
        7109,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso que os capítulos sejam ordenados pela data de publicação.",
    )
    i_praise = _ne(
        7110,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Muito obrigado pelo trabalho incrível da equipe!",
        created_at="2026-11-02T10:00:00Z",
    )
    i_plain = _ne(
        7111,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="_No response_",
        created_at="2026-11-03T10:00:00Z",
    )
    dec_req_vs_praise, _, _ = _eval([i_req, i_praise], 7110)
    assert dec_req_vs_praise.classification == "possible_duplicate"
    assert dec_req_vs_praise.should_auto_close is False

    dec_praise_vs_plain, _, _ = _eval([i_praise, i_plain], 7111)
    assert dec_praise_vs_plain.classification == "confirmed_duplicate"
    assert dec_praise_vs_plain.should_auto_close is True


# ---------------------------------------------------------------------------
# Items 5, 6, 7: Concurrent notes edits before comment, before label, before close
# ---------------------------------------------------------------------------

def test_v170_sec2_concurrent_notes_edit_before_comment_aborts_closure():
    i1 = _ne(7112, "Sushi Manga", "https://sushimanga.com")
    i2 = _ne(7113, "Sushi Manga", "https://sushimanga.com", created_at="2026-11-02T10:00:00Z")
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])
    assert dec.should_auto_close is True

    class EditNotesBeforeCommentApi(InMemoryGitHubApi):
        def get_issue(self, issue_number: int) -> dict:
            if issue_number == 7113 and "ordenados pela data" not in self.issues[7113]["body"]:
                self.issues[7113]["body"] = _ne(
                    7113,
                    "Sushi Manga",
                    "https://sushimanga.com",
                    notes="Preciso que os capítulos sejam ordenados pela data de publicação.",
                    created_at="2026-11-02T10:00:00Z",
                ).body
            return super().get_issue(issue_number)

    api = EditNotesBeforeCommentApi([i1, i2])
    res = GuardActionExecutor(api, _cfg(), {7112: i1, 7113: i2}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert api.issues[7113]["state"] == "open"
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[7113]["labels"]}
    assert not any(w[0] == "close_issue" for w in api.writes)


def test_v170_sec2_concurrent_notes_edit_before_label_aborts_without_duplicate_label():
    i1 = _ne(7114, "Sushi Manga", "https://sushimanga.com")
    i2 = _ne(7115, "Sushi Manga", "https://sushimanga.com", created_at="2026-11-02T10:00:00Z")
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])
    assert dec.should_auto_close is True

    class EditNotesBeforeLabelApi(InMemoryGitHubApi):
        def create_comment(self, issue_number: int, body: str) -> dict:
            res = super().create_comment(issue_number, body)
            if issue_number == 7115:
                self.issues[7115]["body"] = _ne(
                    7115,
                    "Sushi Manga",
                    "https://sushimanga.com",
                    notes="Preciso que os capítulos sejam agrupados pelo nome do autor.",
                    created_at="2026-11-02T10:00:00Z",
                ).body
            return res

    api = EditNotesBeforeLabelApi([i1, i2])
    res = GuardActionExecutor(api, _cfg(), {7114: i1, 7115: i2}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert res.action_taken == "aborted_concurrent_edit_before_label"
    assert api.issues[7115]["state"] == "open"
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[7115]["labels"]}
    assert not any(w[0] == "close_issue" for w in api.writes)


def test_v170_sec2_concurrent_notes_edit_before_close_rolls_back_label_and_never_closes():
    i1 = _ne(7116, "Sushi Manga", "https://sushimanga.com")
    i2 = _ne(7117, "Sushi Manga", "https://sushimanga.com", created_at="2026-11-02T10:00:00Z")
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])
    assert dec.should_auto_close is True

    class EditNotesBeforeCloseApi(InMemoryGitHubApi):
        def add_labels(self, issue_number: int, labels: list[str]) -> None:
            super().add_labels(issue_number, labels)
            if issue_number == 7117 and LABEL_DUPLICATE in labels:
                self.issues[7117]["body"] = _ne(
                    7117,
                    "Sushi Manga",
                    "https://sushimanga.com",
                    notes="Preciso que os capítulos sejam ordenados pela data de publicação.",
                    created_at="2026-11-02T10:00:00Z",
                ).body

    api = EditNotesBeforeCloseApi([i1, i2])
    res = GuardActionExecutor(api, _cfg(), {7116: i1, 7117: i2}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert res.action_taken == "aborted_concurrent_edit_before_close"
    assert api.issues[7117]["state"] == "open"
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[7117]["labels"]}
    assert ("remove_label", 7117, LABEL_DUPLICATE) in api.writes
    assert not any(w[0] == "close_issue" for w in api.writes)


# ---------------------------------------------------------------------------
# Item 8: Editing only notes or structured metadata alters content_fingerprint
# ---------------------------------------------------------------------------

def test_v170_sec2_content_fingerprint_sensitive_to_notes_flags_and_case_sensitive_urls():
    base = parse_issue(_ne(7118, "Sushi Manga", "https://sushimanga.com/Manga", notes="Sem filtros"))
    diff_notes = parse_issue(
        _ne(7118, "Sushi Manga", "https://sushimanga.com/Manga", notes="Preciso de filtro por gênero.")
    )
    diff_login = parse_issue(
        _ne(7118, "Sushi Manga", "https://sushimanga.com/Manga", notes="Sem filtros", login="Sim")
    )
    diff_vip = parse_issue(
        _ne(7118, "Sushi Manga", "https://sushimanga.com/Manga", notes="Sem filtros", vip="Sim")
    )
    diff_nsfw = parse_issue(
        _ne(7118, "Sushi Manga", "https://sushimanga.com/Manga", notes="Sem filtros", nsfw="Sim")
    )
    diff_path_case = parse_issue(
        _ne(7118, "Sushi Manga", "https://sushimanga.com/manga", notes="Sem filtros")
    )
    diff_query_case_a = parse_issue(_ne(7118, "Sushi Manga", "https://sushimanga.com/watch?id=ABC123"))
    diff_query_case_b = parse_issue(_ne(7118, "Sushi Manga", "https://sushimanga.com/watch?id=abc123"))

    # Volatile metadata edits (updated_at / labels / comments) MUST NOT change fingerprint
    volatile_edited = make_raw(
        7118,
        base.raw.title,
        base.raw.body,
        labels=["Nova extensão", "Mangá", "Em análise", "Aceito"],
        created_at=base.raw.created_at,
        updated_at="2026-11-20T23:59:59Z",
    )
    volatile_parsed = parse_issue(volatile_edited)

    assert base.notes == "Sem filtros"
    assert base.content_fingerprint == volatile_parsed.content_fingerprint
    for modified in (diff_notes, diff_login, diff_vip, diff_nsfw, diff_path_case):
        assert modified.content_fingerprint != base.content_fingerprint
    assert diff_query_case_a.content_fingerprint != diff_query_case_b.content_fingerprint


# ---------------------------------------------------------------------------
# Item 9: Stale cache invalidation after RULE_ENGINE_VERSION bump and notes edit
# ---------------------------------------------------------------------------

def test_v170_sec4_stale_v160_cache_invalidated_and_notes_edit_re_evaluated(tmp_path, capsys):
    assert RULE_ENGINE_VERSION >= "1.7.0"
    i1 = _ne(
        7119,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso que os capítulos sejam ordenados pela data de publicação.",
    )
    i2 = _ne(
        7120,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso que os capítulos sejam agrupados pelo nome do autor.",
        created_at="2026-11-02T10:00:00Z",
    )
    cache_file = tmp_path / "cache_160.json"
    cache_file.write_text(
        json.dumps({
            "rule_version": "1.6.0",
            "last_full_backfill_at": "2026-11-21T06:00:00Z",
            "evaluated": {
                "7120": {
                    "updated_at": i2.updated_at,
                    "fingerprint": parse_issue(i2).content_fingerprint,
                    "catalog_digest": "empty",
                    "auto_close_enabled": True,
                    "decision": "confirmed_duplicate",
                    "canonical_issue": 7119,
                    "canonical_state": "open",
                    "action_taken": "auto_closed",
                }
            },
            "dup_links": {},
            "catalog": {},
        }),
        encoding="utf-8",
    )
    api = InMemoryGitHubApi([i1, i2])
    rc = run_cli(["--mode", "reconcile", "--cache-file", str(cache_file)], client_override=api)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rule_version"] == RULE_ENGINE_VERSION
    assert out["effective_mode"] == "backfill"
    assert out["skipped_cached_count"] == 0
    assert api.issues[7120]["state"] == "open"
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[7120]["labels"]}


# ---------------------------------------------------------------------------
# Items 10 & 11: URL path capitalization and functional query parameter case
# ---------------------------------------------------------------------------

def test_v170_sec3_url_path_case_difference_preserved_open():
    u_cap = normalize_url("https://site.com/Manga")
    u_low = normalize_url("https://site.com/manga")
    assert u_cap is not None and u_low is not None
    assert u_cap.scope_path == "/Manga"
    assert u_low.scope_path == "/manga"
    assert u_cap.destination_key != u_low.destination_key
    assert u_cap.source_identity_key != u_low.source_identity_key

    i1 = _ne(7121, "Site Caps", "https://site.com/Manga")
    i2 = _ne(7122, "Site Caps", "https://site.com/manga", created_at="2026-11-02T10:00:00Z")
    dec, _, _ = _eval([i1, i2], 7122)
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_v170_sec3_functional_query_param_case_preserved_while_tracking_ignored():
    u_up = normalize_url("https://site.com/watch?id=ABC123&utm_source=APP")
    u_lo = normalize_url("https://site.com/watch?id=abc123&utm_source=web")
    u_eq = normalize_url("https://site.com/watch?id=ABC123&cb=999")
    assert u_up is not None and u_lo is not None and u_eq is not None
    assert u_up.functional_query == "id=ABC123"
    assert u_lo.functional_query == "id=abc123"
    assert u_eq.functional_query == "id=ABC123"
    assert u_up.source_identity_key != u_lo.source_identity_key
    assert u_up.source_identity_key == u_eq.source_identity_key

    i1 = _ne(7123, "Site Watch", "https://site.com/watch?id=ABC123&utm_source=APP", ext_type="Anime")
    i2 = _ne(
        7124,
        "Site Watch",
        "https://site.com/watch?id=abc123&utm_source=web",
        ext_type="Anime",
        created_at="2026-11-02T10:00:00Z",
    )
    i3 = _ne(
        7125,
        "Site Watch",
        "https://site.com/watch?id=ABC123&cb=999",
        ext_type="Anime",
        created_at="2026-11-03T10:00:00Z",
    )
    dec_diff, _, _ = _eval([i1, i2], 7124)
    dec_same, _, _ = _eval([i1, i3], 7125)
    assert dec_diff.classification == "possible_duplicate"
    assert dec_diff.should_auto_close is False
    assert dec_same.classification == "confirmed_duplicate"
    assert dec_same.should_auto_close is True


# ---------------------------------------------------------------------------
# Item 12: Genuine identical duplicates continue auto-closing accurately
# ---------------------------------------------------------------------------

def test_v170_sec1_3_true_duplicates_still_auto_close():
    i1 = _ne(7126, "Sushi Manga", "HTTP://WWW.SUSHIMANGA.COM/Manga?id=ABC123")
    i2 = _ne(
        7127,
        "SushiManga",
        "https://sushimanga.com/Manga?id=ABC123&utm_medium=share",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, _, _ = _eval([i1, i2], 7127)
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True


# ---------------------------------------------------------------------------
# Item 13: Direct verification of _revalidate_live_decision & _verify_concurrent_barrier
# ---------------------------------------------------------------------------

def test_v170_sec2_direct_revalidation_and_barrier_detect_notes_mutation_on_equivalent_pair():
    obs_a = "Preciso que os capítulos sejam ordenados pela data de publicação."
    obs_b = "Preciso que os capítulos sejam agrupados pelo nome do autor."
    i1 = _ne(7128, "Sushi Manga", "https://sushimanga.com", notes=obs_a)
    i2 = _ne(7129, "Sushi Manga", "https://sushimanga.com", notes=obs_a, created_at="2026-11-02T10:00:00Z")
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True

    # Mutate notes on live copy to conflicting requirement
    edited_i2 = _ne(7129, "Sushi Manga", "https://sushimanga.com", notes=obs_b, created_at="2026-11-02T10:00:00Z")
    api = InMemoryGitHubApi([i1, edited_i2])
    executor = GuardActionExecutor(api, _cfg(), {7128: i1, 7129: i2}, matcher=matcher)

    reval_dec, live_parsed, _, note = executor._revalidate_live_decision(dec, edited_i2, [])
    assert note == "live_revalidated"
    assert live_parsed.content_fingerprint != dec.fingerprint
    assert reval_dec.classification == "possible_duplicate"
    assert reval_dec.should_auto_close is False

    ok_barrier, barrier_reason, _ = executor._verify_concurrent_barrier(
        7129,
        dec.fingerprint,
        dec,
        parsed[0],
        own_comment_id=None,
    )
    assert ok_barrier is False
    assert barrier_reason == "target_edited_during_execution"
    assert not any(w[0] == "close_issue" for w in api.writes)

