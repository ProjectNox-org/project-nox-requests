# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Comprehensive v1.6.0 audit regression suite (66 non-trivial scenarios across all 6 audit blocks)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.duplicate_guard.actions import GuardActionExecutor  # noqa: E402
from scripts.duplicate_guard.cli import parse_corpus, run_cli  # noqa: E402
from scripts.duplicate_guard.config import load_config  # noqa: E402
from scripts.duplicate_guard.github_client import GitHubApiError, RateLimitError  # noqa: E402
from scripts.duplicate_guard.matcher import DuplicateMatcher  # noqa: E402
from scripts.duplicate_guard.models import LABEL_DONE, LABEL_DUPLICATE, RULE_ENGINE_VERSION  # noqa: E402
from scripts.duplicate_guard.normalize import extract_specific_work_hint, normalize_url  # noqa: E402
from scripts.duplicate_guard.parser import build_extension_catalog, parse_issue  # noqa: E402
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
    created_at: str = "2026-11-01T10:00:00Z",
    state: str = "open",
    state_reason: str | None = None,
    labels: list[str] | None = None,
    author: str = "user1",
):
    lbls = labels or ["Nova extensão", ext_type, "Em análise"]
    body = (
        f"### Nome da extensão\n{name}\n"
        f"### Tipo\n{ext_type}\n"
        f"### Idioma\n{lang}\n"
        f"### URL do site\n{url}\n"
        f"### Observações adicionais\n{notes}\n"
    )
    return make_raw(
        num,
        f"[Nova extensão]: {name}",
        body,
        labels=lbls,
        state=state,
        state_reason=state_reason,
        created_at=created_at,
        author=author,
    )


def _bug(
    num: int,
    title: str,
    desc: str,
    *,
    ext: str = "Sushi Manga",
    ext_type: str = "Mangá",
    version: str = "1.4.2",
    part: str = "Capítulos",
    url: str = "",
    created_at: str = "2026-11-01T10:00:00Z",
    state: str = "open",
    labels: list[str] | None = None,
    author: str = "user1",
):
    lbls = labels or ["Problema na extensão", ext_type, "Em análise"]
    url_block = f"### URL do site\n{url}\n" if url else ""
    body = (
        f"### Nome da extensão\n{ext}\n"
        f"### Tipo\n{ext_type}\n"
        f"### Versão da extensão\n{version}\n"
        f"### Parte afetada\n{part}\n"
        f"{url_block}"
        f"### O que está acontecendo?\n{desc}\n"
    )
    return make_raw(num, title, body, labels=lbls, state=state, created_at=created_at, author=author)


def _dc(
    num: int,
    name: str,
    old_url: str,
    new_url: str,
    *,
    ext_type: str = "Mangá",
    lang: str = "Português",
    created_at: str = "2026-11-01T10:00:00Z",
):
    body = (
        f"### Nome da extensão\n{name}\n"
        f"### Tipo\n{ext_type}\n"
        f"### Idioma\n{lang}\n"
        f"### URL antiga\n{old_url}\n"
        f"### URL nova\n{new_url}\n"
    )
    return make_raw(num, f"[Mudança de domínio]: {name}", body, labels=["Mudança de domínio", ext_type, "Em análise"], created_at=created_at)


def _ds(
    num: int,
    name: str,
    url: str,
    desc: str,
    *,
    ext_type: str = "Mangá",
    created_at: str = "2026-11-01T10:00:00Z",
):
    body = (
        f"### Nome da extensão\n{name}\n"
        f"### Tipo\n{ext_type}\n"
        f"### URL do site\n{url}\n"
        f"### O que aconteceu?\n{desc}\n"
    )
    return make_raw(num, f"[Fonte indisponível]: {name}", body, labels=["Fonte indisponível", ext_type, "Em análise"], created_at=created_at)


def _enh(num: int, title: str, summary: str, desc: str, *, ext: str = "Sushi Manga", created_at: str = "2026-11-01T10:00:00Z"):
    body = (
        f"### Título ou resumo\n{summary}\n"
        f"### Descrição da melhoria\n{desc}\n"
        f"### Extensão relacionada (opcional)\n{ext}\n"
    )
    return make_raw(num, title, body, labels=["Sugestão", "Em análise"], created_at=created_at)


def _eval(issues, target_num, *, catalog=None):
    parsed = parse_corpus(issues)
    target = next(p for p in parsed if p.raw.number == target_num)
    matcher = DuplicateMatcher(parsed, _cfg(), catalog=catalog)
    return matcher.evaluate(target), target, parsed


# ===========================================================================
# Block 1: Official Catalog Source-Level Identity (16 tests)
# ===========================================================================

def test_c1_01_sec32_catalog_free_subdomain_does_not_fulfill_vip_request():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Nox: Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "pt-BR",
            "version": "1.0",
            "sources": [{"name": "Sushi Manga", "id": "1", "lang": "pt-BR", "baseUrl": "https://free.sushimanga.com"}],
        }]
    })
    req = _ne(101, "Sushi Manga", "https://vip.sushimanga.com", lang="Português")
    dec, _, _ = _eval([req], 101, catalog=cat)
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False
    assert dec.blocked_reason == "catalog_source_url_or_path_mismatch"


def test_c1_02_catalog_free_subpath_does_not_fulfill_vip_subpath():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Nox: Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "pt-BR",
            "sources": [{"name": "Sushi Manga Free", "id": "1", "lang": "pt-BR", "baseUrl": "https://sushimanga.com/free"}],
        }]
    })
    req = _ne(102, "Sushi Manga", "https://sushimanga.com/vip", lang="Português")
    dec, _, _ = _eval([req], 102, catalog=cat)
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_c1_03_catalog_www2_does_not_auto_fulfill_www_request():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "pt-BR",
            "sources": [{"name": "Sushi Manga", "id": "1", "lang": "pt-BR", "baseUrl": "https://www2.sushimanga.com"}],
        }]
    })
    req = _ne(103, "Sushi Manga", "https://www.sushimanga.com", lang="Português")
    dec, _, _ = _eval([req], 103, catalog=cat)
    assert dec.should_auto_close is False
    assert dec.classification == "no_match"


def test_c1_04_catalog_mobile_m_subdomain_does_not_auto_fulfill_root():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "pt-BR",
            "sources": [{"name": "Sushi Manga", "id": "1", "lang": "pt-BR", "baseUrl": "https://m.sushimanga.com"}],
        }]
    })
    req = _ne(104, "Sushi Manga", "https://sushimanga.com", lang="Português")
    dec, _, _ = _eval([req], 104, catalog=cat)
    assert dec.should_auto_close is False
    assert dec.classification == "no_match"


def test_c1_05_catalog_non_default_port_does_not_fulfill_standard_port():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "pt-BR",
            "sources": [{"name": "Sushi Manga", "id": "1", "lang": "pt-BR", "baseUrl": "https://sushimanga.com:8443"}],
        }]
    })
    req = _ne(105, "Sushi Manga", "https://sushimanga.com", lang="Português")
    dec, _, _ = _eval([req], 105, catalog=cat)
    assert dec.should_auto_close is False
    assert dec.classification == "no_match"


def test_c1_06_catalog_multiple_packages_matching_same_exact_source_blocks_close():
    cat = build_extension_catalog({
        "manga": [
            {"name": "Sushi Manga", "pkg": "eu.pkg.one", "lang": "pt-BR", "sources": [{"name": "Sushi Manga", "id": "1", "lang": "pt-BR", "baseUrl": "https://sushimanga.com"}]},
            {"name": "Sushi Manga", "pkg": "eu.pkg.two", "lang": "pt-BR", "sources": [{"name": "Sushi Manga", "id": "2", "lang": "pt-BR", "baseUrl": "https://sushimanga.com"}]},
        ]
    })
    req = _ne(106, "Sushi Manga", "https://sushimanga.com", lang="Português")
    dec, _, _ = _eval([req], 106, catalog=cat)
    assert dec.should_auto_close is False
    assert dec.blocked_reason == "live_catalog_ambiguous_multiple_packages"


def test_c1_07_catalog_source_missing_baseurl_never_closes():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "pt-BR",
            "sources": [{"name": "Sushi Manga", "id": "1", "lang": "pt-BR", "baseUrl": ""}],
        }]
    })
    req = _ne(107, "Sushi Manga", "https://sushimanga.com", lang="Português")
    dec, _, _ = _eval([req], 107, catalog=cat)
    assert dec.should_auto_close is False
    assert dec.classification == "no_match"


def test_c1_08_multisource_apk_distinguishes_subdomains_inside_same_package():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Pack",
            "pkg": "eu.pkg.sushipack",
            "lang": "all",
            "sources": [
                {"name": "Sushi Alpha", "id": "1", "lang": "pt-BR", "baseUrl": "https://alpha.sushimanga.com"},
                {"name": "Sushi Beta", "id": "2", "lang": "pt-BR", "baseUrl": "https://beta.sushimanga.com"},
            ],
        }]
    })
    req_exact = _ne(108, "Sushi Alpha", "https://alpha.sushimanga.com", lang="Português")
    req_gamma = _ne(109, "Sushi Alpha", "https://gamma.sushimanga.com", lang="Português")
    dec_exact, _, _ = _eval([req_exact], 108, catalog=cat)
    dec_gamma, _, _ = _eval([req_gamma], 109, catalog=cat)
    assert dec_exact.classification == "confirmed_duplicate" and dec_exact.should_auto_close is True
    assert dec_gamma.classification == "no_match" and dec_gamma.should_auto_close is False


def test_c1_09_sec9_canonical_selection_rejects_free_ticket_for_vip_catalog_match():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "pt-BR",
            "sources": [
                {"name": "Sushi Manga Free", "id": "1", "lang": "pt-BR", "baseUrl": "https://free.sushimanga.com"},
                {"name": "Sushi Manga VIP", "id": "2", "lang": "pt-BR", "baseUrl": "https://vip.sushimanga.com"},
            ],
        }]
    })
    t_free = _ne(500, "Sushi Manga Free", "https://free.sushimanga.com", state="closed", state_reason="completed", labels=["Nova extensão", "Mangá", "Concluído"])
    t_vip = _ne(501, "Sushi Manga VIP", "https://vip.sushimanga.com", created_at="2026-11-10T10:00:00Z")
    dec, _, _ = _eval([t_free, t_vip], 501, catalog=cat)
    assert dec.classification == "confirmed_duplicate"
    assert dec.match_kind == "already_fulfilled_creation"
    assert dec.should_auto_close is True
    assert dec.canonical_issue is None
    assert dec.resolution_label == LABEL_DONE


def test_c1_10_canonical_selection_ignores_declined_and_cancelled_prior_tickets():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "pt-BR",
            "sources": [{"name": "Sushi Manga", "id": "1", "lang": "pt-BR", "baseUrl": "https://sushimanga.com"}],
        }]
    })
    t_declined = _ne(502, "Sushi Manga", "https://sushimanga.com", state="closed", labels=["Nova extensão", "Mangá", "Recusado"])
    t_new = _ne(503, "Sushi Manga", "https://sushimanga.com", created_at="2026-11-10T10:00:00Z")
    dec, _, _ = _eval([t_declined, t_new], 503, catalog=cat)
    assert dec.should_auto_close is True
    assert dec.canonical_issue is None
    assert dec.resolution_label == LABEL_DONE


def test_c1_11_bug_different_source_subdomains_blocked_hard():
    b1 = _bug(504, "Sushi Manga — Erro", "No pages found no leitor.", part="Leitor / Imagens", url="https://free.sushimanga.com")
    b2 = _bug(505, "Sushi Manga — Erro", "No pages found no leitor.", part="Leitor / Imagens", url="https://vip.sushimanga.com", created_at="2026-11-02T10:00:00Z")
    dec, _, _ = _eval([b1, b2], 505)
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_c1_12_catalog_functional_query_mismatch_blocks_fulfillment():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "pt-BR",
            "sources": [{"name": "Sushi Manga", "id": "1", "lang": "pt-BR", "baseUrl": "https://sushimanga.com/?cat=free"}],
        }]
    })
    req = _ne(506, "Sushi Manga", "https://sushimanga.com/?cat=vip", lang="Português")
    dec, _, _ = _eval([req], 506, catalog=cat)
    assert dec.should_auto_close is False
    assert dec.classification == "no_match"


def test_c1_13_catalog_positive_www_equivalence_auto_closes():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "pt-BR",
            "sources": [{"name": "Sushi Manga", "id": "1", "lang": "pt-BR", "baseUrl": "https://www.sushimanga.com"}],
        }]
    })
    req = _ne(507, "Sushi Manga", "http://sushimanga.com/", lang="Português")
    dec, _, _ = _eval([req], 507, catalog=cat)
    assert dec.classification == "confirmed_duplicate"
    assert dec.match_kind == "already_fulfilled_creation"
    assert dec.should_auto_close is True
    assert dec.resolution_label == LABEL_DONE


def test_c1_14_catalog_positive_home_alias_equivalence_auto_closes():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "pt-BR",
            "sources": [{"name": "Sushi Manga", "id": "1", "lang": "pt-BR", "baseUrl": "https://sushimanga.com"}],
        }]
    })
    req = _ne(508, "Sushi Manga", "https://www.sushimanga.com/home", lang="Português")
    dec, _, _ = _eval([req], 508, catalog=cat)
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True


def test_c1_15_catalog_missing_pkg_never_closes():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "",
            "lang": "pt-BR",
            "sources": [{"name": "Sushi Manga", "id": "1", "lang": "pt-BR", "baseUrl": "https://sushimanga.com"}],
        }]
    })
    req = _ne(509, "Sushi Manga", "https://sushimanga.com", lang="Português")
    dec, _, _ = _eval([req], 509, catalog=cat)
    assert dec.should_auto_close is False
    assert dec.blocked_reason == "catalog_incomplete_or_contradictory_metadata"


def test_c1_16_catalog_wrong_ext_type_never_fulfills():
    cat = build_extension_catalog({
        "anime": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.anime.sushimanga",
            "lang": "pt-BR",
            "sources": [{"name": "Sushi Manga", "id": "1", "lang": "pt-BR", "baseUrl": "https://sushimanga.com"}],
        }]
    })
    req = _ne(510, "Sushi Manga", "https://sushimanga.com", ext_type="Mangá", lang="Português")
    dec, _, _ = _eval([req], 510, catalog=cat)
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


# ===========================================================================
# Block 2: Per-Source Language <-> URL Bindings in Catalog (13 tests)
# ===========================================================================

def test_c2_01_sec42_multisource_language_bound_to_en_url_blocks_pt_request():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.all.sushimanga",
            "lang": "all",
            "sources": [
                {"name": "Sushi Manga", "id": "1", "lang": "en", "baseUrl": "https://en.sushimanga.com"},
                {"name": "Sushi Manga", "id": "2", "lang": "pt-BR", "baseUrl": "https://pt.sushimanga.com"},
            ],
        }]
    })
    req = _ne(201, "Sushi Manga", "https://en.sushimanga.com", lang="Português")
    dec, _, _ = _eval([req], 201, catalog=cat)
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False
    assert dec.blocked_reason == "catalog_source_language_mismatch"
    assert "[en]" in dec.reasons[0]


def test_c2_02_sec42_multisource_matching_pt_url_and_pt_language_closes():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.all.sushimanga",
            "lang": "all",
            "sources": [
                {"name": "Sushi Manga", "id": "1", "lang": "en", "baseUrl": "https://en.sushimanga.com"},
                {"name": "Sushi Manga", "id": "2", "lang": "pt-BR", "baseUrl": "https://pt.sushimanga.com"},
            ],
        }]
    })
    req = _ne(202, "Sushi Manga", "https://pt.sushimanga.com", lang="Português")
    dec, _, _ = _eval([req], 202, catalog=cat)
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True


def test_c2_03_multisource_language_bound_by_url_path_blocks_cross_locale():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.all.sushimanga",
            "lang": "all",
            "sources": [
                {"name": "Sushi Manga EN", "id": "1", "lang": "en", "baseUrl": "https://sushimanga.com/en"},
                {"name": "Sushi Manga PT", "id": "2", "lang": "pt-BR", "baseUrl": "https://sushimanga.com/pt"},
            ],
        }]
    })
    req = _ne(203, "Sushi Manga EN", "https://sushimanga.com/en", lang="Português")
    dec, _, _ = _eval([req], 203, catalog=cat)
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_c2_04_multisource_omitted_source_lang_does_not_inherit_package_lang():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "pt-BR",
            "sources": [
                {"name": "Sushi One", "id": "1", "lang": "en", "baseUrl": "https://sushimanga.com"},
                {"name": "Sushi Two", "id": "2", "lang": "", "baseUrl": "https://alt.sushimanga.com"},
            ],
        }]
    })
    req = _ne(204, "Sushi Two", "https://alt.sushimanga.com", lang="Português")
    dec, _, _ = _eval([req], 204, catalog=cat)
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_c2_05_single_source_empty_lang_inherits_concrete_package_lang():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "pt-BR",
            "sources": [{"name": "Sushi Manga", "id": "1", "lang": "", "baseUrl": "https://sushimanga.com"}],
        }]
    })
    req_pt = _ne(205, "Sushi Manga", "https://sushimanga.com", lang="Português")
    req_en = _ne(206, "Sushi Manga", "https://sushimanga.com", lang="Inglês")
    dec_pt, _, _ = _eval([req_pt], 205, catalog=cat)
    dec_en, _, _ = _eval([req_en], 206, catalog=cat)
    assert dec_pt.classification == "confirmed_duplicate" and dec_pt.should_auto_close is True
    assert dec_en.classification == "no_match" and dec_en.should_auto_close is False


def test_c2_06_single_source_conflicting_entry_vs_source_lang_blocks_close():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "pt-BR",
            "sources": [{"name": "Sushi Manga", "id": "1", "lang": "en", "baseUrl": "https://sushimanga.com"}],
        }]
    })
    req = _ne(207, "Sushi Manga", "https://sushimanga.com", lang="Inglês")
    dec, _, _ = _eval([req], 207, catalog=cat)
    assert dec.should_auto_close is False
    assert dec.blocked_reason == "catalog_incomplete_or_contradictory_metadata"


def test_c2_07_source_url_locale_contradicts_declared_source_lang_blocks_close():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "pt-BR",
            "sources": [{"name": "Sushi Manga", "id": "1", "lang": "pt-BR", "baseUrl": "https://en.sushimanga.com"}],
        }]
    })
    req = _ne(208, "Sushi Manga", "https://en.sushimanga.com", lang="Português")
    dec, _, _ = _eval([req], 208, catalog=cat)
    assert dec.should_auto_close is False
    assert dec.blocked_reason == "catalog_incomplete_or_contradictory_metadata"


def test_c2_08_unknown_catalog_or_requested_language_never_closes():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "tlh",
            "sources": [{"name": "Sushi Manga", "id": "1", "lang": "klingon_custom", "baseUrl": "https://sushimanga.com"}],
        }]
    })
    req = _ne(209, "Sushi Manga", "https://sushimanga.com", lang="Tagalog")
    dec, _, _ = _eval([req], 209, catalog=cat)
    assert dec.should_auto_close is False


def test_c2_09_all_languages_requested_against_en_plus_es_without_all_never_closes():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "all",
            "sources": [
                {"name": "Sushi Manga", "id": "1", "lang": "en", "baseUrl": "https://sushimanga.com"},
                {"name": "Sushi Manga", "id": "2", "lang": "es", "baseUrl": "https://sushimanga.com"},
            ],
        }]
    })
    req = _ne(210, "Sushi Manga", "https://sushimanga.com", lang="Todos os idiomas")
    dec, _, _ = _eval([req], 210, catalog=cat)
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_c2_10_all_languages_requested_against_source_with_all_closes():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "all",
            "sources": [{"name": "Sushi Manga", "id": "1", "lang": "all", "baseUrl": "https://sushimanga.com"}],
        }]
    })
    req = _ne(211, "Sushi Manga", "https://sushimanga.com", lang="Todos os idiomas")
    dec, _, _ = _eval([req], 211, catalog=cat)
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True


def test_c2_11_two_sources_same_exact_url_cover_pt_and_en():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "all",
            "sources": [
                {"name": "Sushi Manga", "id": "1", "lang": "en", "baseUrl": "https://sushimanga.com"},
                {"name": "Sushi Manga", "id": "2", "lang": "pt-BR", "baseUrl": "https://sushimanga.com"},
            ],
        }]
    })
    req = _ne(212, "Sushi Manga", "https://sushimanga.com", lang="Português")
    dec, _, _ = _eval([req], 212, catalog=cat)
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True


def test_c2_12_matching_source_with_extra_language_observation_blocks_close():
    cat = build_extension_catalog({
        "manga": [{
            "name": "Sushi Manga",
            "pkg": "eu.pkg.sushimanga",
            "lang": "pt-BR",
            "sources": [{"name": "Sushi Manga", "id": "1", "lang": "pt-BR", "baseUrl": "https://sushimanga.com"}],
        }]
    })
    req = _ne(
        213,
        "Sushi Manga",
        "https://sushimanga.com",
        lang="Português",
        notes="A extensão já existe mas preciso adicionar suporte ao conteúdo em espanhol também.",
    )
    dec, _, _ = _eval([req], 213, catalog=cat)
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_c2_13_valid_package_plus_contradictory_second_package_blocks_close():
    cat = build_extension_catalog({
        "manga": [
            {"name": "Sushi Manga", "pkg": "eu.pkg.good", "lang": "pt-BR", "sources": [{"name": "Sushi Manga", "id": "1", "lang": "pt-BR", "baseUrl": "https://sushimanga.com"}]},
            {"name": "Sushi Manga", "pkg": "eu.pkg.bad", "lang": "pt-BR", "sources": [{"name": "Sushi Manga", "id": "2", "lang": "en", "baseUrl": "https://sushimanga.com"}]},
        ]
    })
    req = _ne(214, "Sushi Manga", "https://sushimanga.com", lang="Português")
    dec, _, _ = _eval([req], 214, catalog=cat)
    assert dec.should_auto_close is False


# ===========================================================================
# Block 3: Paths, Subdomains, Ports, and Queries (17 tests)
# ===========================================================================

def test_c3_01_sec51_webtoons_vs_novels_preserved_open():
    i1 = _ne(301, "Sushi Manga", "https://sushimanga.com/webtoons")
    i2 = _ne(302, "Sushi Manga", "https://sushimanga.com/novels", created_at="2026-11-02T10:00:00Z")
    dec, _, _ = _eval([i1, i2], 302)
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_c3_02_path_pt_vs_en_never_auto_closes():
    i1 = _ne(303, "Sushi Manga", "https://sushimanga.com/pt", lang="Português")
    i2 = _ne(304, "Sushi Manga", "https://sushimanga.com/en", lang="Inglês", created_at="2026-11-02T10:00:00Z")
    dec_diff_lang, _, _ = _eval([i1, i2], 304)
    assert dec_diff_lang.classification == "no_match" and dec_diff_lang.should_auto_close is False

    i3 = _ne(305, "Sushi Manga", "https://sushimanga.com/en", lang="Português", created_at="2026-11-02T10:00:00Z")
    dec_same_decl, _, _ = _eval([i1, i3], 305)
    assert dec_same_decl.classification == "possible_duplicate" and dec_same_decl.should_auto_close is False


def test_c3_03_path_manga_vs_anime_never_auto_closes():
    i1 = _ne(306, "Sushi Hub", "https://sushihub.com/manga")
    i2 = _ne(307, "Sushi Hub", "https://sushihub.com/anime", created_at="2026-11-02T10:00:00Z")
    dec, _, _ = _eval([i1, i2], 307)
    assert dec.should_auto_close is False


def test_c3_04_path_free_vs_vip_never_auto_closes():
    i1 = _ne(308, "Sushi Manga", "https://sushimanga.com/free")
    i2 = _ne(309, "Sushi Manga", "https://sushimanga.com/vip", created_at="2026-11-02T10:00:00Z")
    dec, _, _ = _eval([i1, i2], 309)
    assert dec.should_auto_close is False


def test_c3_05_path_api_v1_vs_api_v2_never_auto_closes():
    i1 = _ne(310, "Sushi Manga", "https://sushimanga.com/api/v1")
    i2 = _ne(311, "Sushi Manga", "https://sushimanga.com/api/v2", created_at="2026-11-02T10:00:00Z")
    dec, _, _ = _eval([i1, i2], 311)
    assert dec.should_auto_close is False


def test_c3_06_path_catalog_vs_reader_never_auto_closes():
    i1 = _ne(312, "Sushi Manga", "https://sushimanga.com/catalog")
    i2 = _ne(313, "Sushi Manga", "https://sushimanga.com/reader", created_at="2026-11-02T10:00:00Z")
    dec, _, _ = _eval([i1, i2], 313)
    assert dec.should_auto_close is False


def test_c3_07_safe_homepage_and_trailing_slash_equivalences_close():
    # "/" vs "/", "/" vs "/home", and "/manga/" vs "/manga"
    p1 = _ne(314, "Sushi Manga", "https://sushimanga.com/")
    p2 = _ne(315, "Sushi Manga", "https://sushimanga.com/home", created_at="2026-11-02T10:00:00Z")
    d12, _, _ = _eval([p1, p2], 315)
    assert d12.classification == "confirmed_duplicate" and d12.should_auto_close is True

    m1 = _ne(316, "Sushi Manga", "https://sushimanga.com/manga/")
    m2 = _ne(317, "Sushi Manga", "https://sushimanga.com/manga", created_at="2026-11-02T10:00:00Z")
    dm, _, _ = _eval([m1, m2], 317)
    assert dm.classification == "confirmed_duplicate" and dm.should_auto_close is True


def test_c3_08_relative_url_with_base_domain_context_handled_accurately():
    i_abs = _ne(318, "Sushi Manga", "https://sushimanga.com/webtoons")
    i_rel_same = _ne(319, "Sushi Manga", "/webtoons", notes="Site oficial em https://sushimanga.com", created_at="2026-11-02T10:00:00Z")
    i_rel_diff = _ne(320, "Sushi Manga", "/novels", notes="Site oficial em https://sushimanga.com", created_at="2026-11-02T11:00:00Z")
    # Clear non-substantive note check by placing URL inside a standalone body section via raw make_raw
    i_rel_same = make_raw(
        319,
        "[Nova extensão]: Sushi Manga",
        "### Nome da extensão\nSushi Manga\n### Tipo\nMangá\n### Idioma\nPortuguês\n### URL do site\n/webtoons\n### Informações complementares (opcional)\nhttps://sushimanga.com\n",
        labels=["Nova extensão", "Mangá", "Em análise"],
        created_at="2026-11-02T10:00:00Z",
    )
    i_rel_diff = make_raw(
        320,
        "[Nova extensão]: Sushi Manga",
        "### Nome da extensão\nSushi Manga\n### Tipo\nMangá\n### Idioma\nPortuguês\n### URL do site\n/novels\n### Informações complementares (opcional)\nhttps://sushimanga.com\n",
        labels=["Nova extensão", "Mangá", "Em análise"],
        created_at="2026-11-02T11:00:00Z",
    )
    dec_same, _, _ = _eval([i_abs, i_rel_same], 319)
    dec_diff, _, _ = _eval([i_abs, i_rel_diff], 320)
    assert dec_same.classification == "confirmed_duplicate" and dec_same.should_auto_close is True
    assert dec_diff.classification == "possible_duplicate" and dec_diff.should_auto_close is False


def test_c3_09_http_https_percent_encoding_and_host_case_folding_equivalences():
    i1 = _ne(321, "Sushi Manga", "HTTP://SUSHIMANGA.COM/web%74oons/")
    i2 = _ne(322, "Sushi Manga", "https://www.sushimanga.com/webtoons", created_at="2026-11-02T10:00:00Z")
    dec, _, _ = _eval([i1, i2], 322)
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True


def test_c3_10_functional_query_strings_diverge_while_volatile_params_match():
    q_web = _ne(323, "Sushi Manga", "https://sushimanga.com/?type=webtoon&utm_source=app")
    q_nov = _ne(324, "Sushi Manga", "https://sushimanga.com/?type=novel", created_at="2026-11-02T10:00:00Z")
    q_same = _ne(325, "Sushi Manga", "https://sushimanga.com/?type=webtoon&cb=999", created_at="2026-11-02T11:00:00Z")
    dec_diff, _, _ = _eval([q_web, q_nov], 324)
    dec_same, _, _ = _eval([q_web, q_same], 325)
    assert dec_diff.classification == "possible_duplicate" and dec_diff.should_auto_close is False
    assert dec_same.classification == "confirmed_duplicate" and dec_same.should_auto_close is True


def test_c3_11_www_vs_root_confirmed_duplicate():
    i1 = _ne(326, "Sushi Manga", "https://www.site.com")
    i2 = _ne(327, "Sushi Manga", "https://site.com", created_at="2026-11-02T10:00:00Z")
    dec, _, _ = _eval([i1, i2], 327)
    assert dec.classification == "confirmed_duplicate" and dec.should_auto_close is True


def test_c3_12_sec61_www2_vs_www_preserved_open():
    i1 = _ne(328, "Sushi Manga", "https://www2.site.com")
    i2 = _ne(329, "Sushi Manga", "https://www.site.com", created_at="2026-11-02T10:00:00Z")
    dec, _, _ = _eval([i1, i2], 329)
    assert dec.classification == "possible_duplicate" and dec.should_auto_close is False


def test_c3_13_mobile_m_vs_root_preserved_open():
    i1 = _ne(330, "Sushi Manga", "https://m.site.com")
    i2 = _ne(331, "Sushi Manga", "https://site.com", created_at="2026-11-02T10:00:00Z")
    dec, _, _ = _eval([i1, i2], 331)
    assert dec.classification == "possible_duplicate" and dec.should_auto_close is False


def test_c3_14_api_pt_en_v1_v2_subdomains_never_auto_close():
    for idx, (u_a, u_b) in enumerate((
        ("https://api.site.com", "https://site.com"),
        ("https://pt.site.com", "https://en.site.com"),
        ("https://v1.site.com", "https://v2.site.com"),
    )):
        a = _ne(340 + idx * 2, "Sushi", u_a)
        b = _ne(341 + idx * 2, "Sushi", u_b, created_at="2026-11-02T10:00:00Z")
        dec, _, _ = _eval([a, b], 341 + idx * 2)
        assert dec.should_auto_close is False


def test_c3_15_ports_8443_vs_default_vs_443():
    p_8443 = _ne(350, "Sushi", "https://site.com:8443")
    p_def = _ne(351, "Sushi", "https://site.com", created_at="2026-11-02T10:00:00Z")
    p_443 = _ne(352, "Sushi", "https://site.com:443", created_at="2026-11-02T11:00:00Z")
    dec_diff, _, _ = _eval([p_8443, p_def], 351)
    dec_443, _, _ = _eval([p_def, p_443], 352)
    assert dec_diff.classification == "possible_duplicate" and dec_diff.should_auto_close is False
    assert dec_443.classification == "confirmed_duplicate" and dec_443.should_auto_close is True


def test_c3_16_site_com_vs_site_net_never_auto_closes():
    i1 = _ne(353, "MoonSite", "https://site.com")
    i2 = _ne(354, "MoonSite", "https://site.net", created_at="2026-11-02T10:00:00Z")
    dec, _, _ = _eval([i1, i2], 354)
    assert dec.classification == "possible_duplicate" and dec.should_auto_close is False


def test_c3_17_shared_hosts_github_io_and_pages_dev_no_match():
    g1 = _ne(355, "AlphaScan", "https://usuario1.github.io")
    g2 = _ne(356, "AlphaScan", "https://usuario2.github.io", created_at="2026-11-02T10:00:00Z")
    p1 = _ne(357, "BetaScan", "https://projeto1.pages.dev")
    p2 = _ne(358, "BetaScan", "https://projeto2.pages.dev", created_at="2026-11-02T10:00:00Z")
    dg, _, _ = _eval([g1, g2], 356)
    dp, _, _ = _eval([p1, p2], 358)
    assert dg.should_auto_close is False and dg.candidates[0].contradictions == ("different_domains",)
    assert dp.should_auto_close is False and dp.candidates[0].contradictions == ("different_domains",)


# ===========================================================================
# Block 4: Work Expressions & Bug Scopes (11 tests)
# ===========================================================================

def test_c4_01_sec71_na_serie_naruto_vs_bleach_with_shared_endpoint_preserved():
    b1 = _bug(401, "[Problema na extensão]: Sushi Manga — Erro em capítulos", "Na série Naruto, ocorre JsonDecodingException em /api/chapters.")
    b2 = _bug(402, "[Problema na extensão]: Sushi Manga — Erro em capítulos", "Na série Bleach, ocorre JsonDecodingException em /api/chapters.", created_at="2026-11-02T10:00:00Z")
    dec, p2, parsed = _eval([b1, b2], 402)
    assert parsed[0].specific_work_hint == "naruto"
    assert p2.specific_work_hint == "bleach"
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_c4_02_no_titulo_variants_block_false_closure():
    b1 = _bug(403, "[Problema na extensão]: Sushi Manga — Falha", "No título Naruto aparece JsonDecodingException em /api/chapters.")
    b2 = _bug(404, "[Problema na extensão]: Sushi Manga — Falha", "No título One Piece aparece JsonDecodingException em /api/chapters.", created_at="2026-11-02T10:00:00Z")
    dec, p2, parsed = _eval([b1, b2], 404)
    assert parsed[0].specific_work_hint == "naruto"
    assert p2.specific_work_hint == "one piece"
    assert dec.classification == "no_match" and dec.should_auto_close is False


def test_c4_03_na_historia_and_ao_abrir_expressions():
    b1 = _bug(405, "[Problema na extensão]: Sushi Manga — Erro", "Na história Naruto não carrega o capítulo.")
    b2 = _bug(406, "[Problema na extensão]: Sushi Manga — Erro", "Ao abrir Bleach não carrega o capítulo.", created_at="2026-11-02T10:00:00Z")
    dec, p2, parsed = _eval([b1, b2], 406)
    assert parsed[0].specific_work_hint == "naruto"
    assert p2.specific_work_hint == "bleach"
    assert dec.classification == "no_match" and dec.should_auto_close is False


def test_c4_04_exclusivo_and_somente_em_expressions():
    b1 = _bug(407, "[Problema na extensão]: Sushi Manga — Falha", "Problema exclusivo de Naruto: No pages found no leitor.", part="Leitor / Imagens")
    b2 = _bug(408, "[Problema na extensão]: Sushi Manga — Falha", "Ocorre somente em Bleach com No pages found no leitor.", part="Leitor / Imagens", created_at="2026-11-02T10:00:00Z")
    dec, p2, parsed = _eval([b1, b2], 408)
    assert parsed[0].specific_work_hint == "naruto"
    assert p2.specific_work_hint == "bleach"
    assert dec.classification == "no_match" and dec.should_auto_close is False


def test_c4_05_multi_work_naruto_e_bleach_vs_single_work():
    b1 = _bug(409, "[Problema na extensão]: Sushi Manga — Obras", "Ocorre em Naruto e Bleach ao tentar abrir capítulos.")
    b2 = _bug(410, "[Problema na extensão]: Sushi Manga — Obra", "Ocorre na série Naruto ao tentar abrir capítulos.", created_at="2026-11-02T10:00:00Z")
    dec, p2, parsed = _eval([b1, b2], 410)
    assert parsed[0].specific_work_hint == "bleach + naruto"
    assert p2.specific_work_hint == "naruto"
    assert dec.classification == "no_match" and dec.should_auto_close is False


def test_c4_06_todas_as_obras_vs_specific_work_never_auto_closes():
    b1 = _bug(411, "[Problema na extensão]: Sushi Manga — Todas as obras", "Todas as obras apresentam JsonDecodingException em /api/chapters.")
    b2 = _bug(412, "[Problema na extensão]: Sushi Manga — Série", "Na série Naruto apresenta JsonDecodingException em /api/chapters.", created_at="2026-11-02T10:00:00Z")
    dec, p2, parsed = _eval([b1, b2], 412)
    assert parsed[0].has_global_work_scope is True
    assert parsed[0].specific_work_hint == ""
    assert p2.specific_work_hint == "naruto"
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_c4_07_um_capitulo_especifico_unnamed_never_auto_closes():
    b1 = _bug(413, "[Problema na extensão]: Sushi Manga — Erro", "Um capítulo específico apresenta JsonDecodingException ao abrir.")
    b2 = _bug(414, "[Problema na extensão]: Sushi Manga — Erro", "Um capítulo específico apresenta JsonDecodingException ao abrir.", created_at="2026-11-02T10:00:00Z")
    dec, p2, parsed = _eval([b1, b2], 414)
    assert parsed[0].has_unnamed_specific_work_scope is True
    assert parsed[0].specific_work_hint == ""
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_c4_08_generic_terms_never_fabricated_as_work_titles():
    for sample in (
        "Ao abrir leitor aparece erro.",
        "Erro ao tentar fazer login.",
        "Problema na busca.",
        "No capítulo ocorre erro.",
        "Qualquer capítulo falha.",
        "Todas as obras estão quebradas.",
        "Um capítulo específico falha.",
    ):
        assert extract_specific_work_hint(sample, sample, "Sushi Manga") == ""


def test_c4_09_positive_true_bug_duplicate_with_matching_series_chapter_endpoint():
    b1 = _bug(415, "[Problema na extensão]: Sushi Manga — Naruto cap 12", "Na série Naruto, capítulo 12 falha com JsonDecodingException ao chamar https://api.sushimanga.com.br/api/chapters/12.")
    b2 = _bug(416, "[Problema na extensão]: Sushi Manga — Cap 12 de Naruto", "No título Naruto, capítulo 12 retorna JsonDecodingException em GET /api/chapters/12.", created_at="2026-11-02T10:00:00Z")
    dec, _, _ = _eval([b1, b2], 416)
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True


def test_c4_10_dead_source_different_subdomains_and_vip_scope_preserved():
    d1 = _ds(417, "Sushi Manga", "https://free.sushimanga.com", "Domínio expirou e equipe confirmou encerramento definitivo.")
    d2 = _ds(418, "Sushi Manga", "https://vip.sushimanga.com", "Domínio expirou e equipe confirmou encerramento definitivo na área VIP.", created_at="2026-11-02T10:00:00Z")
    dec, _, _ = _eval([d1, d2], 418)
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_c4_11_domain_change_divergent_old_subdomain_or_language_preserved():
    dc1 = _dc(419, "Sushi Manga", "https://pt.sushi.old", "https://sushimanga.com")
    dc2 = _dc(420, "Sushi Manga", "https://en.sushi.old", "https://sushimanga.com", created_at="2026-11-02T10:00:00Z")
    dec, _, _ = _eval([dc1, dc2], 420)
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


# ===========================================================================
# Block 5: Live Revalidation, Pre-Close Barriers & Concurrency (6 tests)
# ===========================================================================

def test_c5_01_live_catalog_portuguese_source_removed_before_close_aborts():
    init_cat = {
        "manga": [{
            "name": "Sushi Manga", "pkg": "eu.pkg.sushimanga", "lang": "all",
            "sources": [
                {"name": "Sushi EN", "id": "1", "lang": "en", "baseUrl": "https://en.sushimanga.com"},
                {"name": "Sushi PT", "id": "2", "lang": "pt-BR", "baseUrl": "https://pt.sushimanga.com"},
            ],
        }]
    }
    live_cat = {
        "manga": [{
            "name": "Sushi Manga", "pkg": "eu.pkg.sushimanga", "lang": "all",
            "sources": [{"name": "Sushi EN", "id": "1", "lang": "en", "baseUrl": "https://en.sushimanga.com"}],
        }]
    }
    req = _ne(520, "Sushi PT", "https://pt.sushimanga.com", lang="Português")
    parsed = parse_corpus([req])
    matcher = DuplicateMatcher(parsed, _cfg(), catalog=build_extension_catalog(init_cat))
    dec = matcher.evaluate(parsed[0])
    assert dec.should_auto_close is True

    api = InMemoryGitHubApi([req], catalog_payload=live_cat)
    res = GuardActionExecutor(api, _cfg(), {520: req}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert api.issues[520]["state"] == "open"
    assert LABEL_DONE not in {l["name"] for l in api.issues[520]["labels"]}


def test_c5_02_live_catalog_subdomain_downgraded_at_pre_close_barrier_rolls_back_label():
    vip_entry = [{"name": "Sushi Manga", "pkg": "eu.pkg.sushimanga", "lang": "pt-BR", "sources": [{"name": "Sushi VIP", "id": "1", "lang": "pt-BR", "baseUrl": "https://vip.sushimanga.com"}]}]
    free_entry = [{"name": "Sushi Manga", "pkg": "eu.pkg.sushimanga", "lang": "pt-BR", "sources": [{"name": "Sushi Free", "id": "1", "lang": "pt-BR", "baseUrl": "https://free.sushimanga.com"}]}]

    req = _ne(521, "Sushi VIP", "https://vip.sushimanga.com", lang="Português")
    parsed = parse_corpus([req])
    matcher = DuplicateMatcher(parsed, _cfg(), catalog=build_extension_catalog({"manga": vip_entry}))
    dec = matcher.evaluate(parsed[0])
    assert dec.should_auto_close is True

    class SubdomainChangedAtBarrierApi(InMemoryGitHubApi):
        def __init__(self, issues):
            super().__init__(issues, catalog_payload={"manga": vip_entry, "anime": []})
            self.fetches = 0

        def fetch_catalog_json(self, repo_slug: str, path: str = "index.min.json"):
            self.fetches += 1
            return free_entry if self.fetches >= 2 else vip_entry

    api = SubdomainChangedAtBarrierApi([req])
    res = GuardActionExecutor(api, _cfg(), {521: req}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert api.issues[521]["state"] == "open"
    assert LABEL_DONE not in {l["name"] for l in api.issues[521]["labels"]}
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[521]["labels"]}


def test_c5_03_live_catalog_keeps_url_but_drops_pt_language_at_barrier_rolls_back():
    pt_entry = [{"name": "Sushi Manga", "pkg": "eu.pkg.sushimanga", "lang": "pt-BR", "sources": [{"name": "Sushi", "id": "1", "lang": "pt-BR", "baseUrl": "https://sushimanga.com"}]}]
    es_entry = [{"name": "Sushi Manga", "pkg": "eu.pkg.sushimanga", "lang": "es", "sources": [{"name": "Sushi", "id": "1", "lang": "es", "baseUrl": "https://sushimanga.com"}]}]

    req = _ne(522, "Sushi", "https://sushimanga.com", lang="Português")
    parsed = parse_corpus([req])
    matcher = DuplicateMatcher(parsed, _cfg(), catalog=build_extension_catalog({"manga": pt_entry}))
    dec = matcher.evaluate(parsed[0])
    assert dec.should_auto_close is True

    class LangDroppedAtBarrierApi(InMemoryGitHubApi):
        def __init__(self, issues):
            super().__init__(issues, catalog_payload={"manga": pt_entry, "anime": []})
            self.fetches = 0

        def fetch_catalog_json(self, repo_slug: str, path: str = "index.min.json"):
            self.fetches += 1
            return es_entry if self.fetches >= 2 else pt_entry

    api = LangDroppedAtBarrierApi([req])
    res = GuardActionExecutor(api, _cfg(), {522: req}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert LABEL_DONE not in {l["name"] for l in api.issues[522]["labels"]}


def test_c5_04_concurrent_path_edit_between_label_and_close_rolls_back_duplicate_label():
    i1 = _ne(523, "Sushi Manga", "https://sushimanga.com/webtoons")
    i2 = _ne(524, "Sushi Manga", "https://sushimanga.com/webtoons", created_at="2026-11-02T10:00:00Z")
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])
    assert dec.should_auto_close is True

    class EditPathAtBarrierApi(InMemoryGitHubApi):
        def add_labels(self, issue_number: int, labels: list[str]) -> None:
            super().add_labels(issue_number, labels)
            if issue_number == 524 and LABEL_DUPLICATE in labels:
                self.issues[524]["body"] = self.issues[524]["body"].replace("/webtoons", "/novels")

    api = EditPathAtBarrierApi([i1, i2])
    res = GuardActionExecutor(api, _cfg(), {523: i1, 524: i2}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert api.issues[524]["state"] == "open"
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[524]["labels"]}


def test_c5_05_author_comment_clarifying_vip_subdomain_blocks_closure():
    i1 = _ne(525, "Sushi Manga", "https://sushimanga.com", author="alice")
    i2 = _ne(526, "Sushi Manga", "https://sushimanga.com", created_at="2026-11-02T10:00:00Z", author="bob")
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])
    assert dec.should_auto_close is True

    api = InMemoryGitHubApi(
        [i1, i2],
        initial_comments={526: [{"id": 91, "user": {"login": "bob", "type": "User"}, "body": "Na verdade preciso de https://vip.sushimanga.com."}]},
    )
    res = GuardActionExecutor(api, _cfg(), {525: i1, 526: i2}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert api.issues[526]["state"] == "open"
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[526]["labels"]}


def test_c5_06_live_catalog_api_or_rate_limit_failure_fails_closed():
    cat = {"manga": [{"name": "Sushi", "pkg": "eu.pkg.sushi", "lang": "pt-BR", "sources": [{"name": "Sushi", "id": "1", "lang": "pt-BR", "baseUrl": "https://sushimanga.com"}]}], "anime": []}
    req = _ne(527, "Sushi", "https://sushimanga.com", lang="Português")
    parsed = parse_corpus([req])
    matcher = DuplicateMatcher(parsed, _cfg(), catalog=build_extension_catalog(cat))
    dec = matcher.evaluate(parsed[0])
    assert dec.should_auto_close is True

    api_err = InMemoryGitHubApi([req])
    api_err.fail_on["fetch_catalog_json"] = GitHubApiError("500 internal error", status=500)
    res_err = GuardActionExecutor(api_err, _cfg(), {527: req}, matcher=matcher).apply(dec)
    assert res_err.closed is False

    api_rl = InMemoryGitHubApi([req])
    api_rl.fail_on["fetch_catalog_json"] = RateLimitError("429 rate limit", status=429)
    res_rl = GuardActionExecutor(api_rl, _cfg(), {527: req}, matcher=matcher).apply(dec)
    assert res_rl.closed is False and api_rl.issues[527]["state"] == "open"


# ===========================================================================
# Block 6: Cache Migration v1.5.0 -> v1.6.0, Fingerprints & Enhancements (5 tests)
# ===========================================================================

def test_c6_01_cache_migrates_from_v150_to_v160_and_blocks_stale_path_close(tmp_path, capsys):
    assert RULE_ENGINE_VERSION >= "1.6.0"
    i1 = _ne(601, "Sushi Manga", "https://sushimanga.com/webtoons")
    i2 = _ne(602, "Sushi Manga", "https://sushimanga.com/novels", created_at="2026-11-02T10:00:00Z")
    cache_file = tmp_path / "cache_150.json"
    cache_file.write_text(
        json.dumps({
            "rule_version": "1.5.0",
            "last_full_backfill_at": "2026-11-21T05:00:00Z",
            "evaluated": {
                "602": {
                    "updated_at": i2.updated_at,
                    "fingerprint": parse_issue(i2).content_fingerprint,
                    "catalog_digest": "empty",
                    "auto_close_enabled": True,
                    "decision": "confirmed_duplicate",
                    "canonical_issue": 601,
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
    assert api.issues[602]["state"] == "open"
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[602]["labels"]}


def test_c6_02_catalog_digest_changes_when_source_baseurl_or_source_lang_changes():
    c1 = build_extension_catalog({
        "manga": [{
            "name": "Sushi", "pkg": "eu.pkg.sushi", "lang": "all",
            "sources": [{"name": "Sushi", "id": "1", "lang": "en", "baseUrl": "https://free.sushimanga.com"}],
        }]
    })
    c2 = build_extension_catalog({
        "manga": [{
            "name": "Sushi", "pkg": "eu.pkg.sushi", "lang": "all",
            "sources": [{"name": "Sushi", "id": "1", "lang": "en", "baseUrl": "https://vip.sushimanga.com"}],
        }]
    })
    c3 = build_extension_catalog({
        "manga": [{
            "name": "Sushi", "pkg": "eu.pkg.sushi", "lang": "all",
            "sources": [{"name": "Sushi", "id": "1", "lang": "pt-BR", "baseUrl": "https://free.sushimanga.com"}],
        }]
    })
    assert c1.digest() != c2.digest()
    assert c1.digest() != c3.digest()


def test_c6_03_fingerprint_and_url_normalization_sensitive_to_path_and_query():
    u_a = normalize_url("https://sushimanga.com/webtoons?cat=a&utm_source=x")
    u_b = normalize_url("https://sushimanga.com/webtoons?cat=b")
    assert u_a is not None and u_b is not None
    assert u_a.source_identity_key != u_b.source_identity_key
    p_a = parse_issue(_ne(603, "Sushi", "https://sushimanga.com/webtoons?cat=a"))
    p_b = parse_issue(_ne(604, "Sushi", "https://sushimanga.com/novels?cat=a"))
    assert p_a.content_fingerprint != p_b.content_fingerprint


def test_c6_04_enhancement_distinguishes_login_vs_registration():
    e_login = _enh(
        605,
        "[Sugestão]: Sushi Manga — Login",
        "Permitir fazer login com conta existente na extensão Sushi Manga",
        "Gostaria de poder fazer login usando minha conta já cadastrada no site.",
    )
    e_reg = _enh(
        606,
        "[Sugestão]: Sushi Manga — Cadastro",
        "Permitir cadastrar nova conta pela extensão Sushi Manga",
        "Seria ótimo conseguir criar cadastro de nova conta diretamente no app.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec, _, _ = _eval([e_login, e_reg], 606)
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_c6_05_cli_reconcile_restores_v160_cache_with_zero_reprocessing(tmp_path, capsys):
    i1 = _ne(607, "Sushi Manga", "https://sushimanga.com/webtoons")
    i2 = _ne(608, "Sushi Manga", "https://sushimanga.com/webtoons", created_at="2026-11-02T10:00:00Z")
    api = InMemoryGitHubApi([i1, i2])
    cache_file = tmp_path / "state_160.json"

    rc1 = run_cli(["--mode", "backfill", "--cache-file", str(cache_file)], client_override=api)
    assert rc1 == 0
    out1 = json.loads(capsys.readouterr().out)
    assert out1["rule_version"] == RULE_ENGINE_VERSION
    assert api.issues[608]["state"] == "closed"

    api.api_calls_made = 0
    writes_before = len(api.writes)
    rc2 = run_cli(["--mode", "reconcile", "--cache-file", str(cache_file)], client_override=api)
    assert rc2 == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["processed_via_api_count"] == 0
    assert out2["skipped_cached_count"] == 1
    assert len(api.writes) == writes_before
