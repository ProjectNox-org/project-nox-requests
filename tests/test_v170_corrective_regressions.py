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


# ---------------------------------------------------------------------------
# v1.7.1 Mandatory Adversarial Polarity / Negation / Modality Suite (10 tests)
# ---------------------------------------------------------------------------

def test_v171_adv_01_com_filtro_vs_sem_filtro_por_autor_preserved_open():
    i1 = _ne(7201, "Sushi Manga", "https://sushimanga.com", notes="Quero a extensão com filtro por autor.")
    i2 = _ne(
        7202,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Quero a extensão sem filtro por autor.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, _, _ = _eval([i1, i2], 7202)
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False
    assert "scope_or_observation_divergence" in dec.candidates[0].contradictions


def test_v171_adv_02_permitir_login_vs_bloquear_login_preserved_open():
    i1 = _ne(7203, "Sushi Manga", "https://sushimanga.com", notes="Preciso permitir login na extensão.")
    i2 = _ne(
        7204,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso bloquear login na extensão.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, _, _ = _eval([i1, i2], 7204)
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_v171_adv_03_exibir_conteudo_adulto_vs_ocultar_conteudo_adulto_preserved_open():
    i1 = _ne(7205, "Sushi Manga", "https://sushimanga.com", notes="Preciso exibir conteúdo adulto na listagem.")
    i2 = _ne(
        7206,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso ocultar conteúdo adulto na listagem.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, _, _ = _eval([i1, i2], 7206)
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_v171_adv_04_apenas_portugues_vs_portugues_e_ingles_preserved_open():
    i1 = _ne(7207, "Sushi Manga", "https://sushimanga.com", notes="Preciso que mostre apenas português.")
    i2 = _ne(
        7208,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso que mostre português e inglês.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, _, _ = _eval([i1, i2], 7208)
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_v171_adv_05_ordenacao_crescente_vs_decrescente_preserved_open():
    i1 = _ne(
        7209,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso que os capítulos sejam ordenados em ordem crescente.",
    )
    i2 = _ne(
        7210,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso que os capítulos sejam ordenados em ordem decrescente.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, _, _ = _eval([i1, i2], 7210)
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_v171_adv_06_exigir_autenticacao_vs_dispensar_autenticacao_preserved_open():
    i1 = _ne(
        7211,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso exigir autenticação para acessar o catálogo.",
    )
    i2 = _ne(
        7212,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso dispensar autenticação para acessar o catálogo.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, _, _ = _eval([i1, i2], 7212)
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_v171_adv_07_recurso_obrigatorio_vs_recurso_opcional_preserved_open():
    i1 = _ne(7213, "Sushi Manga", "https://sushimanga.com", notes="O login na extensão deve ser obrigatório.")
    i2 = _ne(
        7214,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="O login na extensão deve ser opcional.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, _, _ = _eval([i1, i2], 7214)
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_v171_adv_08_equivalent_phrases_positive_and_negative_auto_close():
    # Equivalent affirmative phrasings ("com filtro por autor" vs "tenha filtro pelo nome do autor")
    p1 = _ne(7215, "Sushi Manga", "https://sushimanga.com", notes="Quero a extensão com filtro por autor.")
    p2 = _ne(
        7216,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso que a extensão tenha filtro pelo nome do autor.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec_pos, _, _ = _eval([p1, p2], 7216)
    assert dec_pos.classification == "confirmed_duplicate"
    assert dec_pos.should_auto_close is True

    # Equivalent negative phrasings ("sem filtro por autor" vs "sem filtrar pelo nome do autor")
    n1 = _ne(7217, "Nori Manga", "https://norimanga.com", notes="Quero a extensão sem filtro por autor.")
    n2 = _ne(
        7218,
        "Nori Manga",
        "https://norimanga.com",
        notes="Preciso da extensão sem filtrar pelo nome do autor.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec_neg, _, _ = _eval([n1, n2], 7218)
    assert dec_neg.classification == "confirmed_duplicate"
    assert dec_neg.should_auto_close is True


def test_v171_adv_09_true_duplicates_without_additional_observations_auto_close():
    i1 = _ne(7219, "Nori Manga", "https://norimanga.com", notes="_No response_")
    i2 = _ne(
        7220,
        "NoriManga",
        "https://www.norimanga.com/home",
        notes="_No response_",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, _, _ = _eval([i1, i2], 7220)
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True


def test_v171_adv_10_contradictory_observation_edited_during_closing_rolls_back_and_aborts():
    obs_pos = "Quero a extensão com filtro por autor."
    obs_neg = "Quero a extensão sem filtro por autor."
    i1 = _ne(7221, "Sushi Manga", "https://sushimanga.com", notes=obs_pos)
    i2 = _ne(
        7222,
        "Sushi Manga",
        "https://sushimanga.com",
        notes=obs_pos,
        created_at="2026-11-02T10:00:00Z",
    )
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True

    # 1. Edit between evaluation and comment (pre-comment live revalidation)
    api_pre = InMemoryGitHubApi([i1, _ne(7222, "Sushi Manga", "https://sushimanga.com", notes=obs_neg, created_at="2026-11-02T10:00:00Z")])
    res_pre = GuardActionExecutor(api_pre, _cfg(), {7221: i1, 7222: i2}, matcher=matcher).apply(dec)
    assert res_pre.closed is False
    assert api_pre.issues[7222]["state"] == "open"
    assert LABEL_DUPLICATE not in {l["name"] for l in api_pre.issues[7222]["labels"]}
    assert not any(w[0] == "close_issue" for w in api_pre.writes)

    # 2. Edit between comment and label
    class MutateBeforeLabelApi(InMemoryGitHubApi):
        def create_comment(self, issue_number: int, body: str) -> dict:
            r = super().create_comment(issue_number, body)
            if issue_number == 7222:
                self.issues[7222]["body"] = _ne(
                    7222, "Sushi Manga", "https://sushimanga.com", notes=obs_neg, created_at="2026-11-02T10:00:00Z"
                ).body
            return r

    api_mid = MutateBeforeLabelApi([i1, i2])
    res_mid = GuardActionExecutor(api_mid, _cfg(), {7221: i1, 7222: i2}, matcher=matcher).apply(dec)
    assert res_mid.closed is False
    assert res_mid.action_taken == "aborted_concurrent_edit_before_label"
    assert LABEL_DUPLICATE not in {l["name"] for l in api_mid.issues[7222]["labels"]}
    assert not any(w[0] == "close_issue" for w in api_mid.writes)

    # 3. Edit right after Duplicado label and immediately before close_issue() -> rolls back Duplicado label
    class MutateBeforeCloseApi(InMemoryGitHubApi):
        def add_labels(self, issue_number: int, labels: list[str]) -> None:
            super().add_labels(issue_number, labels)
            if issue_number == 7222 and LABEL_DUPLICATE in labels:
                self.issues[7222]["body"] = _ne(
                    7222, "Sushi Manga", "https://sushimanga.com", notes=obs_neg, created_at="2026-11-02T10:00:00Z"
                ).body

    api_close = MutateBeforeCloseApi([i1, i2])
    res_close = GuardActionExecutor(api_close, _cfg(), {7221: i1, 7222: i2}, matcher=matcher).apply(dec)
    assert res_close.closed is False
    assert res_close.action_taken == "aborted_concurrent_edit_before_close"
    assert api_close.issues[7222]["state"] == "open"
    assert LABEL_DUPLICATE not in {l["name"] for l in api_close.issues[7222]["labels"]}
    assert ("remove_label", 7222, LABEL_DUPLICATE) in api_close.writes
    assert not any(w[0] == "close_issue" for w in api_close.writes)


# ---------------------------------------------------------------------------
# v1.7.2 Double-Negation & Multi-Requirement Contextual Integration Suite (10 tests)
# ---------------------------------------------------------------------------

def test_v172_dn_01_bloquear_vs_nao_bloquear_login_preserved_open():
    i1 = _ne(7301, "Sushi Manga", "https://sushimanga.com", notes="Preciso bloquear login na extensão.")
    i2 = _ne(
        7302,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso não bloquear login na extensão.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, _, _ = _eval([i1, i2], 7302)
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False
    assert "scope_or_observation_divergence" in dec.candidates[0].contradictions


def test_v172_dn_02_ocultar_vs_nao_ocultar_conteudo_adulto_preserved_open():
    i1 = _ne(7303, "Sushi Manga", "https://sushimanga.com", notes="Quero ocultar conteúdo adulto.")
    i2 = _ne(
        7304,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Quero não ocultar conteúdo adulto.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, _, _ = _eval([i1, i2], 7304)
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_v172_dn_03_excluir_vs_nao_excluir_obras_em_ingles_preserved_open():
    i1 = _ne(7305, "Sushi Manga", "https://sushimanga.com", notes="Preciso excluir obras em inglês.")
    i2 = _ne(
        7306,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso não excluir obras em inglês.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, _, _ = _eval([i1, i2], 7306)
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_v172_dn_04_permitir_vs_nao_permitir_login_preserved_open():
    i1 = _ne(7307, "Sushi Manga", "https://sushimanga.com", notes="Preciso permitir login na extensão.")
    i2 = _ne(
        7308,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso não permitir login na extensão.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, _, _ = _eval([i1, i2], 7308)
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_v172_dn_05_exibir_vs_nao_exibir_conteudo_preserved_open():
    i1 = _ne(7309, "Sushi Manga", "https://sushimanga.com", notes="Quero exibir conteúdo adulto.")
    i2 = _ne(
        7310,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Quero não exibir conteúdo adulto.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, _, _ = _eval([i1, i2], 7310)
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_v172_dn_06_incluir_vs_nao_incluir_recurso_preserved_open():
    i1 = _ne(7311, "Sushi Manga", "https://sushimanga.com", notes="Preciso incluir filtro por autor.")
    i2 = _ne(
        7312,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Preciso não incluir filtro por autor.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, _, _ = _eval([i1, i2], 7312)
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_v172_dn_07_semantically_equivalent_double_negation_and_affirmative_formulations_auto_close():
    # "não permitir login" ≡ "bloquear login"
    a1 = _ne(7313, "Sushi Manga", "https://sushimanga.com", notes="Preciso não permitir login na extensão.")
    a2 = _ne(
        7314,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Quero bloquear login na extensão.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec_a, _, _ = _eval([a1, a2], 7314)
    assert dec_a.classification == "confirmed_duplicate"
    assert dec_a.should_auto_close is True

    # "não ocultar conteúdo adulto" ≡ "exibir conteúdo adulto"
    b1 = _ne(7315, "Nori Manga", "https://norimanga.com", notes="Quero não ocultar conteúdo adulto.")
    b2 = _ne(
        7316,
        "Nori Manga",
        "https://norimanga.com",
        notes="Preciso exibir conteúdo adulto.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec_b, _, _ = _eval([b1, b2], 7316)
    assert dec_b.classification == "confirmed_duplicate"
    assert dec_b.should_auto_close is True


def test_v172_dn_08_true_duplicates_without_observations_auto_close():
    i1 = _ne(7317, "Wasabi Manga", "https://wasabimanga.com", notes="_No response_")
    i2 = _ne(
        7318,
        "WasabiManga",
        "https://www.wasabimanga.com/",
        notes="_No response_",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, _, _ = _eval([i1, i2], 7318)
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True


def test_v172_dn_09_multiple_requirements_with_negation_on_only_one_preserved_open():
    # Clause boundary prevents negation on the second requirement from inverting or masking divergence
    m1 = _ne(
        7319,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Quero filtro por autor e não ocultar capítulos extras.",
    )
    m2 = _ne(
        7320,
        "Sushi Manga",
        "https://sushimanga.com",
        notes="Quero filtro por autor e ocultar capítulos extras.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec_m, _, _ = _eval([m1, m2], 7320)
    assert dec_m.classification == "possible_duplicate"
    assert dec_m.should_auto_close is False

    # Negation scoped only to first verb must not flip second clause
    s1 = _ne(
        7321,
        "Nori Manga",
        "https://norimanga.com",
        notes="Preciso não bloquear login e ocultar comentários.",
    )
    s2 = _ne(
        7322,
        "Nori Manga",
        "https://norimanga.com",
        notes="Preciso bloquear login e não ocultar comentários.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec_s, _, _ = _eval([s1, s2], 7322)
    assert dec_s.classification == "possible_duplicate"
    assert dec_s.should_auto_close is False


def test_v172_dn_10_concurrent_edit_affirmative_to_negative_blocks_close_and_rolls_back_labels():
    obs_pos = "Quero ocultar conteúdo adulto."
    obs_dbl_neg = "Quero não ocultar conteúdo adulto."
    i1 = _ne(7323, "Sushi Manga", "https://sushimanga.com", notes=obs_pos)
    i2 = _ne(
        7324,
        "Sushi Manga",
        "https://sushimanga.com",
        notes=obs_pos,
        created_at="2026-11-02T10:00:00Z",
    )
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True

    # Mutate live issue between Duplicado label and close_issue() -> aborts and rolls back Duplicado label
    class MutateRightBeforeCloseApi(InMemoryGitHubApi):
        def add_labels(self, issue_number: int, labels: list[str]) -> None:
            super().add_labels(issue_number, labels)
            if issue_number == 7324 and LABEL_DUPLICATE in labels:
                self.issues[7324]["body"] = _ne(
                    7324, "Sushi Manga", "https://sushimanga.com", notes=obs_dbl_neg, created_at="2026-11-02T10:00:00Z"
                ).body

    api = MutateRightBeforeCloseApi([i1, i2])
    res = GuardActionExecutor(api, _cfg(), {7323: i1, 7324: i2}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert res.action_taken == "aborted_concurrent_edit_before_close"
    assert api.issues[7324]["state"] == "open"
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[7324]["labels"]}
    assert ("remove_label", 7324, LABEL_DUPLICATE) in api.writes
    assert not any(w[0] == "close_issue" for w in api.writes)

