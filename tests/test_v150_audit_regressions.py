# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Comprehensive v1.5.0 audit regression suite (68 non-trivial scenarios across Blocks 1-4)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.duplicate_guard.actions import GuardActionExecutor, render_comment_body  # noqa: E402
from scripts.duplicate_guard.cli import parse_corpus, run_cli  # noqa: E402
from scripts.duplicate_guard.config import load_config  # noqa: E402
from scripts.duplicate_guard.github_client import (  # noqa: E402
    GitHubApiError,
    GitHubClient,
    PermissionDeniedError,
    RateLimitError,
)
from scripts.duplicate_guard.matcher import (  # noqa: E402
    DuplicateMatcher,
    _specific_bug_evidence_tokens,
)
from scripts.duplicate_guard.models import (  # noqa: E402
    LABEL_DONE,
    LABEL_DUPLICATE,
    RULE_ENGINE_VERSION,
)
from scripts.duplicate_guard.normalize import (  # noqa: E402
    extract_api_endpoints,
    extract_url_locale,
    normalize_url,
    sanitize_for_markdown,
)
from scripts.duplicate_guard.parser import build_extension_catalog, parse_issue  # noqa: E402
from tests.test_duplicate_guard import (  # noqa: E402
    InMemoryGitHubApi,
    evaluate_pair,
    make_raw,
)


def _cfg():
    return load_config(REPO_ROOT / ".github" / "duplicate-guard.json")


def _bug(
    num: int,
    title: str,
    desc: str,
    *,
    ext: str = "Sushi Manga",
    ext_type: str = "Mangá",
    version: str = "1.4.2",
    app: str = "Mihon",
    part: str = "Leitor / Imagens",
    repro: str = "",
    created_at: str = "2026-11-01T10:00:00Z",
    state: str = "open",
    state_reason: str | None = None,
    labels: list[str] | None = None,
    author: str = "user1",
):
    lbls = labels or ["Problema na extensão", ext_type, "Em análise"]
    body = (
        f"### Tipo de extensão\n{ext_type}\n"
        f"### Nome da extensão\n{ext}\n"
        f"### Versão da extensão\n{version}\n"
        f"### Aplicativo utilizado\n{app}\n"
        f"### Parte afetada\n{part}\n"
        f"### O que está acontecendo?\n{desc}\n"
    )
    if repro:
        body += f"### Como reproduzir?\n{repro}\n"
    return make_raw(
        num,
        title,
        body,
        labels=lbls,
        state=state,
        state_reason=state_reason,
        created_at=created_at,
        author=author,
    )


def _dc(
    num: int,
    name: str,
    old_url: str,
    new_url: str,
    *,
    ext_type: str = "Mangá",
    created_at: str = "2026-11-01T10:00:00Z",
    state: str = "open",
    state_reason: str | None = None,
    labels: list[str] | None = None,
):
    lbls = labels or ["Mudança de domínio", ext_type, "Em análise"]
    body = (
        f"### Tipo de extensão\n{ext_type}\n"
        f"### Nome da extensão\n{name}\n"
        f"### URL antiga\n{old_url}\n"
        f"### URL nova\n{new_url}\n"
    )
    return make_raw(
        num,
        f"[Mudança de domínio]: {name}",
        body,
        labels=lbls,
        state=state,
        state_reason=state_reason,
        created_at=created_at,
    )


def _ne(
    num: int,
    name: str,
    url: str,
    *,
    ext_type: str = "Mangá",
    lang: str = "Português (Brasil)",
    obs: str = "_No response_",
    created_at: str = "2026-11-01T10:00:00Z",
):
    body = (
        f"### Tipo de extensão\n{ext_type}\n"
        f"### Nome da fonte\n{name}\n"
        f"### URL do site\n{url}\n"
        f"### Idioma\n{lang}\n"
        f"### Observações (opcional)\n{obs}\n"
    )
    return make_raw(
        num,
        f"[Nova extensão]: {name}",
        body,
        labels=["Nova extensão", ext_type, "Em análise"],
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# Block 1 — Double-Counting Elimination, Title Scoping & Section 6.1 (17 tests)
# ---------------------------------------------------------------------------

def test_t1_01_sec22_title_only_naruto_vs_one_piece_no_pages_found():
    b200 = _bug(200, "Sushi Manga — Naruto não carrega", "Ao abrir o leitor aparece No pages found.")
    b201 = _bug(201, "Sushi Manga — One Piece não carrega", "Ao abrir o leitor aparece No pages found.", created_at="2026-11-02T10:00:00Z")
    p200, p201 = parse_issue(b200), parse_issue(b201)
    assert p200.specific_work_hint == "naruto"
    assert p201.specific_work_hint == "one piece"
    dec = evaluate_pair([b200, b201], 201, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_t1_02_no_pages_found_tokens_excluded_from_independent_evidence():
    b = _bug(202, "Sushi Manga", "Ao abrir o leitor aparece No pages found.")
    parsed = parse_issue(b)
    indep = _specific_bug_evidence_tokens(
        parsed,
        include_part_words=True,
        exclude_signature_tokens=True,
        exclude_declared_part_tokens=True,
    )
    assert "pages" not in indep
    assert "found" not in indep
    assert "leitor" not in indep
    assert indep == frozenset()


def test_t1_03_sec61_case_b_bare_no_pages_found_never_auto_closes():
    b1 = _bug(203, "Sushi Manga", "No pages found.")
    b2 = _bug(204, "Sushi Manga", "No pages found.", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([b1, b2], 204, _cfg())
    assert dec.should_auto_close is False
    assert dec.classification != "confirmed_duplicate"


def test_t1_04_bare_no_results_found_without_context_never_auto_closes():
    b1 = _bug(205, "Sushi Manga — Sem resultado", "No results found na pesquisa.", part="Busca / Filtros")
    b2 = _bug(206, "Sushi Manga — Sem resultados", "No results found na busca.", part="Busca / Filtros", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([b1, b2], 206, _cfg())
    assert dec.should_auto_close is False


def test_t1_05_bare_json_decoding_exception_without_context_never_auto_closes():
    b1 = _bug(207, "Sushi Manga — JsonDecodingException", "Erro JsonDecodingException.", part="Popular / Recentes")
    b2 = _bug(208, "Sushi Manga — JsonDecodingException", "JsonDecodingException no catálogo.", part="Popular / Recentes", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([b1, b2], 208, _cfg())
    assert dec.should_auto_close is False


def test_t1_06_bare_null_pointer_exception_without_context_never_auto_closes():
    b1 = _bug(209, "Sushi Manga — NPE", "java.lang.NullPointerException.", part="Detalhes")
    b2 = _bug(210, "Sushi Manga — NPE", "java.lang.NullPointerException nos detalhes.", part="Detalhes", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([b1, b2], 210, _cfg())
    assert dec.should_auto_close is False


def test_t1_07_bare_unknown_host_without_host_token_never_auto_closes():
    b1 = _bug(211, "Sushi Manga — UnknownHostException", "UnknownHostException ao carregar.", part="Outro")
    b2 = _bug(212, "Sushi Manga — UnknownHostException", "UnknownHostException.", part="Outro", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([b1, b2], 212, _cfg())
    assert dec.should_auto_close is False


def test_t1_08_sec61_case_a_generic_403_chapters_vs_login():
    b1 = _bug(213, "Sushi Manga — 403 capítulos", "HTTP 403 ao abrir capítulos.", part="Capítulos")
    b2 = _bug(214, "Sushi Manga — 403 login", "HTTP 403 ao tentar fazer login.", part="Login", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([b1, b2], 214, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_t1_09_sec61_case_c_naruto_vs_one_piece_dash_title():
    b1 = _bug(215, "Sushi Manga — Naruto — No pages found.", "No pages found.")
    b2 = _bug(216, "Sushi Manga — One Piece — No pages found.", "No pages found.", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([b1, b2], 216, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_t1_10_sec61_case_d_version_140_vs_145_http_403_regression():
    b1 = _bug(217, "Sushi Manga — HTTP 403", "Erro HTTP 403 na versão 1.4.0.", version="1.4.0", part="Capítulos")
    b2 = _bug(218, "Sushi Manga — HTTP 403", "Erro HTTP 403 na versão 1.4.5.", version="1.4.5", part="Capítulos", created_at="2026-11-03T10:00:00Z")
    dec = evaluate_pair([b1, b2], 218, _cfg())
    assert dec.should_auto_close is False
    assert dec.classification == "possible_duplicate"


def test_t1_11_sec61_case_e_bug_fixed_two_years_ago_preserved():
    b1 = _bug(
        219, "Anime X — Falha de reprodução em 2024", "Falha de reprodução no player.",
        ext="Anime X", ext_type="Anime", part="Vídeo / Player",
        state="closed", state_reason="completed",
        labels=["Problema na extensão", "Anime", "Concluído"],
        created_at="2024-05-01T10:00:00Z",
    )
    b2 = _bug(
        220, "Anime X — Falha de reprodução voltou em 2026", "Falha de reprodução voltou em 2026.",
        ext="Anime X", ext_type="Anime", part="Vídeo / Player",
        created_at="2026-11-01T10:00:00Z",
    )
    dec = evaluate_pair([b1, b2], 220, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_t1_12_sec61_case_f_true_duplicate_with_independent_evidence():
    b1 = _bug(
        221,
        "Sushi Manga — Naruto cap 12 JsonDecodingException",
        "JsonDecodingException Unexpected JSON token ao chamar GET https://api.sushimanga.com.br/api/chapters/12 da obra Naruto.",
        part="Capítulos",
        repro="Abrir obra Naruto -> clicar no capítulo 12.",
    )
    b2 = _bug(
        222,
        "Sushi Manga — Erro capítulo 12 de Naruto",
        "Ao abrir capítulo 12 de Naruto executando GET /api/chapters/12 dá JsonDecodingException Unexpected JSON token.",
        part="Capítulos",
        repro="Selecionar Naruto -> abrir capítulo 12.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([b1, b2], 222, _cfg())
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True


def test_t1_13_unilateral_title_subject_preserves_target_open():
    b1 = _bug(223, "Sushi Manga — Modo Webtoon travado", "JsonDecodingException Unexpected JSON token <!DOCTYPE html> ao abrir leitor.")
    b2 = _bug(224, "Sushi Manga — Erro JSON no leitor", "JsonDecodingException Unexpected JSON token <!DOCTYPE html> ao abrir leitor.", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([b1, b2], 224, _cfg())
    assert dec.should_auto_close is False


def test_t1_14_repro_navigation_boilerplate_does_not_corrupt_work_hint():
    b = _bug(
        225,
        "Erro No pages found no leitor",
        "Ao tentar ler um capítulo aparece No pages found.",
        ext="Bakai",
        version="1.6.21",
        repro="Entrar no mangá e tentar abrir qualquer capítulo.",
    )
    p = parse_issue(b)
    assert p.specific_work_hint == ""


def test_t1_15_work_hint_in_repro_still_extracted_when_named():
    b1 = _bug(226, "Sushi Manga — No pages found", "No pages found ao abrir leitor.", repro="Entrar na obra Jujutsu Kaisen e abrir capítulo 5.")
    b2 = _bug(227, "Sushi Manga — No pages found", "No pages found ao abrir leitor.", repro="Entrar na obra Chainsaw Man e abrir capítulo 5.", created_at="2026-11-02T10:00:00Z")
    p1, p2 = parse_issue(b1), parse_issue(b2)
    assert p1.specific_work_hint == "jujutsu kaisen"
    assert p2.specific_work_hint == "chainsaw man"
    dec = evaluate_pair([b1, b2], 227, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_t1_16_dropdown_chapters_vs_free_text_login_exposes_contradiction():
    b1 = _bug(228, "Sushi Manga — Erro HTTP 403 capítulos", "HTTP 403 ao carregar lista de capítulos.", part="Capítulos")
    b2 = _bug(229, "Sushi Manga — HTTP 403 login", "Erro HTTP 403 ao tentar fazer login na minha conta.", part="Capítulos", created_at="2026-11-02T10:00:00Z")
    p2 = parse_issue(b2)
    assert "login" in p2.affected_parts
    dec = evaluate_pair([b1, b2], 229, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_t1_17_ssl_and_timeout_constituent_tokens_not_double_counted():
    b1 = _bug(230, "Sushi Manga — SSL", "SSLHandshakeException certificado inválido no leitor.", part="Leitor / Imagens")
    b2 = _bug(231, "Sushi Manga — Timeout", "SocketTimeoutException tempo esgotado no leitor.", part="Leitor / Imagens", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([b1, b2], 231, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


# ---------------------------------------------------------------------------
# Block 2 — API Endpoint Extraction & Matching (17 tests — Sec 3.4 #1–#15+)
# ---------------------------------------------------------------------------

def test_t2_01_isolated_endpoints_different():
    b1 = _bug(300, "Sushi Manga — /api/search", "JsonDecodingException ao chamar /api/search.")
    b2 = _bug(301, "Sushi Manga — /api/details", "JsonDecodingException ao chamar /api/details.", created_at="2026-11-02T10:00:00Z")
    p1, p2 = parse_issue(b1), parse_issue(b2)
    assert "/api/search" in p1.api_endpoints
    assert "/api/details" in p2.api_endpoints
    dec = evaluate_pair([b1, b2], 301, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_t2_02_sec32_full_urls_different_endpoints_in_code_blocks_or_parens():
    b1 = _bug(302, "Sushi Manga — Erro API", "JsonDecodingException ao consultar `https://api.sushimanga.com.br/api/search/`.")
    b2 = _bug(303, "Sushi Manga — Erro API", "JsonDecodingException em (https://api.sushimanga.com.br/api/details#tab).", created_at="2026-11-02T10:00:00Z")
    p1, p2 = parse_issue(b1), parse_issue(b2)
    assert "/api/search" in p1.api_endpoints
    assert "/api/details" in p2.api_endpoints
    dec = evaluate_pair([b1, b2], 303, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_t2_03_same_endpoint_absolute_and_relative_formats():
    b1 = _bug(
        304,
        "Sushi Manga — Busca JsonDecodingException",
        "JsonDecodingException Unexpected JSON token <!DOCTYPE html> ao consultar HTTPS://API.SUSHIMANGA.COM.BR/API/SEARCH.",
        part="Busca / Filtros",
    )
    b2 = _bug(
        305,
        "Sushi Manga — Erro busca JSON",
        "Unexpected JSON token <!DOCTYPE html> com JsonDecodingException na rota /api/search.",
        part="Busca / Filtros",
        created_at="2026-11-02T10:00:00Z",
    )
    p1, p2 = parse_issue(b1), parse_issue(b2)
    assert "/api/search" in p1.api_endpoints
    assert "/api/search" in p2.api_endpoints
    dec = evaluate_pair([b1, b2], 305, _cfg())
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True


def test_t2_04_same_domain_different_endpoints():
    b1 = _bug(306, "Sushi Manga — auth", "JsonDecodingException em https://sushimanga.com.br/api/chapters/12.")
    b2 = _bug(307, "Sushi Manga — cap 18", "JsonDecodingException em https://sushimanga.com.br/api/chapters/18.", created_at="2026-11-02T10:00:00Z")
    p1, p2 = parse_issue(b1), parse_issue(b2)
    assert "/api/chapters/12" in p1.api_endpoints
    assert "/api/chapters/18" in p2.api_endpoints
    dec = evaluate_pair([b1, b2], 307, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_t2_05_different_subdomains_same_endpoint():
    b1 = _bug(308, "Sushi Manga — API principal", "JsonDecodingException Unexpected token <!DOCTYPE html> em https://api.sushimanga.com.br/api/chapters/12.")
    b2 = _bug(309, "Sushi Manga — CDN secundário", "JsonDecodingException Unexpected token <!DOCTYPE html> em https://cdn.sushimanga.com.br/api/chapters/12.", created_at="2026-11-02T10:00:00Z")
    p1, p2 = parse_issue(b1), parse_issue(b2)
    assert {"/api/chapters/12", "ephost:api.sushimanga.com.br"} <= p1.api_endpoints
    assert {"/api/chapters/12", "ephost:cdn.sushimanga.com.br"} <= p2.api_endpoints
    dec = evaluate_pair([b1, b2], 309, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_t2_06_same_endpoint_different_http_methods():
    b1 = _bug(310, "Sushi Manga — GET capítulos", "JsonDecodingException em GET /api/chapters.")
    b2 = _bug(311, "Sushi Manga — POST capítulos", "JsonDecodingException ao fazer POST para /api/chapters.", created_at="2026-11-02T10:00:00Z")
    p1, p2 = parse_issue(b1), parse_issue(b2)
    assert "method:get:/api/chapters" in p1.api_endpoints
    assert "method:post:/api/chapters" in p2.api_endpoints
    dec = evaluate_pair([b1, b2], 311, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_t2_07_same_endpoint_different_api_versions():
    b1 = _bug(312, "Sushi Manga — v1", "JsonDecodingException em https://api.sushimanga.com.br/api/v1/search.")
    b2 = _bug(313, "Sushi Manga — v2", "JsonDecodingException em https://api.sushimanga.com.br/api/v2/search.", created_at="2026-11-02T10:00:00Z")
    p1, p2 = parse_issue(b1), parse_issue(b2)
    assert "/api/v1/search" in p1.api_endpoints
    assert "/api/v2/search" in p2.api_endpoints
    dec = evaluate_pair([b1, b2], 313, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_t2_08_same_endpoint_different_functional_query_params():
    b1 = _bug(314, "Sushi Manga — busca pt", "JsonDecodingException em https://api.sushimanga.com.br/api/search?lang=pt&page=1.")
    b2 = _bug(315, "Sushi Manga — busca en", "JsonDecodingException em https://api.sushimanga.com.br/api/search?lang=en&page=1.", created_at="2026-11-02T10:00:00Z")
    p1, p2 = parse_issue(b1), parse_issue(b2)
    assert "/api/search?lang=pt&page=1" in p1.api_endpoints
    assert "/api/search?lang=en&page=1" in p2.api_endpoints
    dec = evaluate_pair([b1, b2], 315, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_t2_09_same_endpoint_percent_encoded_and_volatile_params_normalized():
    b1 = _bug(
        316,
        "Sushi Manga — Busca",
        "JsonDecodingException Unexpected JSON token em https://api.sushimanga.com.br/api/search?page=2&lang=pt%2Dbr&_t=17000001&utm_source=app.",
        part="Busca / Filtros",
    )
    b2 = _bug(
        317,
        "Sushi Manga — Erro busca JSON",
        "JsonDecodingException Unexpected JSON token em https://api.sushimanga.com.br/api/search?lang=pt-br&page=2&cachebuster=xyz999.",
        part="Busca / Filtros",
        created_at="2026-11-02T10:00:00Z",
    )
    p1, p2 = parse_issue(b1), parse_issue(b2)
    assert "/api/search?lang=pt-br&page=2" in p1.api_endpoints
    assert "/api/search?lang=pt-br&page=2" in p2.api_endpoints
    dec = evaluate_pair([b1, b2], 317, _cfg())
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True


def test_t2_10_endpoint_present_only_in_one_issue_never_auto_closes():
    b1 = _bug(318, "Sushi Manga — /api/search", "JsonDecodingException Unexpected JSON token em https://api.sushimanga.com.br/api/search.")
    b2 = _bug(319, "Sushi Manga — Erro JSON", "JsonDecodingException Unexpected JSON token na extensão.", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([b1, b2], 319, _cfg())
    assert dec.should_auto_close is False
    assert dec.classification != "confirmed_duplicate"


def test_t2_11_endpoint_added_later_in_author_comment_blocks_closure():
    b1 = _bug(320, "Sushi Manga — Erro JSON", "JsonDecodingException Unexpected JSON token <!DOCTYPE html> em /api/search.")
    b2 = _bug(321, "Sushi Manga — Erro JSON", "JsonDecodingException Unexpected JSON token <!DOCTYPE html> em /api/search.", created_at="2026-11-02T10:00:00Z", author="alice")
    parsed = parse_corpus([b1, b2])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])
    assert dec.should_auto_close is True

    api = InMemoryGitHubApi(
        [b1, b2],
        initial_comments={
            321: [
                {
                    "id": 9101,
                    "user": {"login": "alice", "type": "User"},
                    "author_association": "NONE",
                    "body": "Na verdade ocorre na rota https://api.sushimanga.com.br/api/details.",
                }
            ]
        },
    )
    res = GuardActionExecutor(api, _cfg(), {320: b1, 321: b2}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert api.issues[321]["state"] == "open"


def test_t2_12_endpoint_edited_between_label_and_close_rolls_back():
    b1 = _bug(322, "Sushi Manga — Erro JSON", "JsonDecodingException Unexpected JSON token <!DOCTYPE html> em /api/search.")
    b2 = _bug(323, "Sushi Manga — Erro JSON", "JsonDecodingException Unexpected JSON token <!DOCTYPE html> em /api/search.", created_at="2026-11-02T10:00:00Z")
    parsed = parse_corpus([b1, b2])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])

    class EditEndpointAtBarrierApi(InMemoryGitHubApi):
        def __init__(self, issues):
            super().__init__(issues)
            self.gets = 0

        def get_issue(self, issue_number: int) -> dict:
            if issue_number == 323:
                self.gets += 1
                if self.gets >= 3:
                    self.issues[323]["body"] = self.issues[323]["body"].replace("/api/search", "/api/details")
            return super().get_issue(issue_number)

    api = EditEndpointAtBarrierApi([b1, b2])
    res = GuardActionExecutor(api, _cfg(), {322: b1, 323: b2}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert api.issues[323]["state"] == "open"
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[323]["labels"]}


def test_t2_13_malformed_url_handled_without_exception():
    malformed = "Falha com https://[invalid-ipv6 e htpps:// e http://:80 e endpoint inválido ///."
    b = _bug(324, "Sushi Manga — URL quebrada", malformed)
    p = parse_issue(b)
    assert isinstance(p.api_endpoints, frozenset)
    assert normalize_url("https://[invalid-ipv6") is None
    assert normalize_url("http://user:pass@site.com:99999/pt") is None


def test_t2_14_url_credentials_and_tokens_never_stored_or_rendered():
    raw_secret_url = "https://admin:SuperSecretPass99@api.sushimanga.com.br/api/auth/eyJhbGciOiJIUzI1NiJ9SecretJwtTok/chapters?token=SecretTok99&apikey=Key99&page=3"
    b1 = _bug(325, f"Erro em {raw_secret_url}", f"JsonDecodingException ao chamar GET {raw_secret_url}.")
    b2 = _bug(326, "Erro capítulos", "JsonDecodingException ao chamar GET https://api.sushimanga.com.br/api/auth/<redacted>/chapters?page=4.", created_at="2026-11-02T10:00:00Z")
    p1 = parse_issue(b1)
    assert "/api/auth/<redacted>/chapters?page=3" in p1.api_endpoints
    for ep in p1.api_endpoints:
        assert "supersecretpass99" not in ep.lower()
        assert "secrettok99" not in ep.lower()
        assert "key99" not in ep.lower()
        assert "secretjwttok" not in ep.lower()
    san = sanitize_for_markdown(raw_secret_url, 200)
    assert "SuperSecretPass99" not in san
    assert "SecretTok99" not in san
    assert "Key99" not in san
    u = normalize_url("https://admin:SuperSecretPass99@sushimanga.com.br/pt")
    assert u is not None and "supersecret" not in u.raw_identifier
    assert evaluate_pair([b1, b2], 326, _cfg()).should_auto_close is False


def test_t2_15_endpoint_inside_stack_trace_extracted_and_distinguished():
    st1 = "retrofit2.HttpException: HTTP 500\n  at ApiClient.fetch(/api/v2/chapters)\n  at OkHttp.execute(Client.kt:42)"
    st2 = "retrofit2.HttpException: HTTP 500\n  at ApiClient.fetch(/api/v2/search)\n  at OkHttp.execute(Client.kt:42)"
    b1 = _bug(327, "Sushi Manga — Stack chapters", st1, part="Capítulos")
    b2 = _bug(328, "Sushi Manga — Stack search", st2, part="Capítulos", created_at="2026-11-02T10:00:00Z")
    p1, p2 = parse_issue(b1), parse_issue(b2)
    assert "/api/v2/chapters" in p1.api_endpoints
    assert "/api/v2/search" in p2.api_endpoints
    dec = evaluate_pair([b1, b2], 328, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_t2_16_endpoint_in_reproduction_section_extracted():
    b1 = _bug(329, "Sushi Manga — erro requisição", "JsonDecodingException.", repro="Requisição GET https://api.sushimanga.com.br/chapters/90 falha.")
    b2 = _bug(330, "Sushi Manga — erro requisição", "JsonDecodingException.", repro="Requisição GET https://api.sushimanga.com.br/chapters/91 falha.", created_at="2026-11-02T10:00:00Z")
    p1, p2 = parse_issue(b1), parse_issue(b2)
    assert "/chapters/90" in p1.api_endpoints
    assert "/chapters/91" in p2.api_endpoints
    dec = evaluate_pair([b1, b2], 330, _cfg())
    assert dec.should_auto_close is False


def test_t2_17_endpoint_alone_without_matching_work_or_evidence_still_conservative():
    ep1 = extract_api_endpoints("GET https://api.site.com/api/search?q=one#frag")
    ep2 = extract_api_endpoints("GET https://api.site.com/api/search?q=two#other")
    assert ep1 != ep2
    b1 = _bug(331, "Sushi Manga", "HTTP 500 em https://api.site.com/api/search?q=one")
    b2 = _bug(332, "Sushi Manga", "HTTP 500 em https://api.site.com/api/search?q=one", created_at="2026-11-02T10:00:00Z")
    # Generic HTTP 500 with sparse context and no strong signature or rich description stays possible_duplicate!
    dec = evaluate_pair([b1, b2], 332, _cfg())
    assert dec.should_auto_close is False


# ---------------------------------------------------------------------------
# Block 3 — Subdomains, Ports, Paths & Hosting (17 tests — Sec 4.3 cases)
# ---------------------------------------------------------------------------

def test_t3_01_sec42_domain_change_pt_vs_en_subdomains_never_auto_closes():
    d1 = _dc(400, "Anime Sushi", "https://animesushi.old", "https://pt.animesushi.com", ext_type="Anime")
    d2 = _dc(401, "Anime Sushi", "https://animesushi.old", "https://en.animesushi.com", ext_type="Anime", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([d1, d2], 401, _cfg())
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_t3_02_domain_change_api_vs_www_subdomains_never_auto_closes():
    d1 = _dc(402, "Sushi", "https://sushi.old", "https://api.sushi.com")
    d2 = _dc(403, "Sushi", "https://sushi.old", "https://www.sushi.com", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([d1, d2], 403, _cfg())
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_t3_03_domain_change_v1_vs_v2_subdomains_never_auto_closes():
    d1 = _dc(404, "Sushi", "https://sushi.old", "https://v1.sushi.com")
    d2 = _dc(405, "Sushi", "https://sushi.old", "https://v2.sushi.com", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([d1, d2], 405, _cfg())
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_t3_04_domain_change_www_vs_root_confirmed():
    d1 = _dc(406, "Sushi", "https://sushi.old", "https://www.sushi.com")
    d2 = _dc(407, "Sushi", "http://sushi.old/", "https://sushi.com/", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([d1, d2], 407, _cfg())
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True


def test_t3_05_domain_change_www2_vs_www_preserved_open():
    d1 = _dc(408, "Sushi", "https://sushi.old", "https://www2.sushi.com")
    d2 = _dc(409, "Sushi", "https://sushi.old", "https://www.sushi.com", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([d1, d2], 409, _cfg())
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_t3_06_domain_change_mobile_m_vs_root_preserved_open():
    d1 = _dc(410, "Sushi", "https://sushi.old", "https://m.sushi.com")
    d2 = _dc(411, "Sushi", "https://sushi.old", "https://sushi.com", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([d1, d2], 411, _cfg())
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_t3_07_domain_change_path_br_vs_en_preserved_open():
    d1 = _dc(412, "Sushi", "https://sushi.old", "https://sushi.com/br")
    d2 = _dc(413, "Sushi", "https://sushi.old", "https://sushi.com/en", created_at="2026-11-02T10:00:00Z")
    u1, u2 = normalize_url("https://sushi.com/br"), normalize_url("https://sushi.com/en")
    assert u1 is not None and u2 is not None
    assert extract_url_locale(u1) == "pt" and extract_url_locale(u2) == "en"
    assert u1.destination_key != u2.destination_key
    dec = evaluate_pair([d1, d2], 413, _cfg())
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_t3_08_domain_change_com_vs_net_no_match():
    d1 = _dc(414, "Sushi", "https://sushi.old", "https://sushi.com")
    d2 = _dc(415, "Sushi", "https://sushi.old", "https://sushi.net", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([d1, d2], 415, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_t3_09_domain_change_github_io_distinct_users():
    d1 = _dc(416, "ScanA", "https://old.com", "https://usuario1.github.io")
    d2 = _dc(417, "ScanA", "https://old.com", "https://usuario2.github.io", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([d1, d2], 417, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_t3_10_domain_change_pages_dev_distinct_projects():
    d1 = _dc(418, "ScanB", "https://old.com", "https://projeto1.pages.dev")
    d2 = _dc(419, "ScanB", "https://old.com", "https://projeto2.pages.dev", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([d1, d2], 419, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_t3_11_domain_change_workers_dev_distinct_projects():
    d1 = _dc(420, "ScanC", "https://old.com", "https://projeto1.workers.dev")
    d2 = _dc(421, "ScanC", "https://old.com", "https://projeto2.workers.dev", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([d1, d2], 421, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_t3_12_domain_change_compound_suffix_com_br_subdomains():
    d1 = _dc(422, "BRScan", "https://brscan.com.br", "https://pt.brscan.com.br")
    d2 = _dc(423, "BRScan", "https://brscan.com.br", "https://en.brscan.com.br", created_at="2026-11-02T10:00:00Z")
    u1, u2 = normalize_url("https://pt.brscan.com.br"), normalize_url("https://en.brscan.com.br")
    assert u1 is not None and u2 is not None
    assert u1.registrable_domain == "brscan.com.br"
    assert u1.subdomain == "pt" and u2.subdomain == "en"
    dec = evaluate_pair([d1, d2], 423, _cfg())
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_t3_13_domain_change_same_host_different_non_default_ports():
    d1 = _dc(424, "PortScan", "https://portscan.old", "https://portscan.com:8443")
    d2 = _dc(425, "PortScan", "https://portscan.old", "https://portscan.com:9443", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([d1, d2], 425, _cfg())
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_t3_14_domain_change_valid_subdomain_transition_not_flagged_degenerate():
    d1 = _dc(426, "SubMove", "https://v1.submove.com", "https://v2.submove.com")
    d2 = _dc(427, "SubMove", "https://v1.submove.com/", "https://www.v2.submove.com", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([d1, d2], 427, _cfg())
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True


def test_t3_15_domain_change_old_url_subdomains_distinct_preserves_open():
    d1 = _dc(428, "OldSub", "https://v1.oldsub.com", "https://new.oldsub.com")
    d2 = _dc(429, "OldSub", "https://v2.oldsub.com", "https://new.oldsub.com", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([d1, d2], 429, _cfg())
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_t3_16_new_extension_distinct_subdomains_api_vs_www_not_confirmed():
    n1 = _ne(430, "PortalSub", "https://www.portalsub.com")
    n2 = _ne(431, "PortalSub", "https://api.portalsub.com", created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([n1, n2], 431, _cfg())
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_t3_17_dead_source_distinct_subdomains_never_confirmed():
    ds1 = make_raw(
        432, "[Fonte indisponível]: SubDead",
        "### Tipo de extensão\nMangá\n### Nome da extensão\nSubDead\n### O que aconteceu?\nEncerramento definitivo confirmado em https://pt.subdead.com.\n",
        labels=["Fonte indisponível", "Mangá"], created_at="2026-11-01T10:00:00Z",
    )
    ds2 = make_raw(
        433, "[Fonte indisponível]: SubDead",
        "### Tipo de extensão\nMangá\n### Nome da extensão\nSubDead\n### O que aconteceu?\nEncerramento definitivo confirmado em https://en.subdead.com.\n",
        labels=["Fonte indisponível", "Mangá"], created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([ds1, ds2], 433, _cfg())
    assert dec.should_auto_close is False


# ---------------------------------------------------------------------------
# Block 4 — GuardActionExecutor, HTTP Errors, Concurrency & Cache (17 tests)
# ---------------------------------------------------------------------------

def _dup_pair(base_num: int = 500, *, author: str = "reporter"):
    i1 = _bug(
        base_num,
        "Sushi Manga — Naruto cap 12 JsonDecodingException",
        "JsonDecodingException Unexpected JSON token em https://api.sushimanga.com.br/api/chapters/12 na obra Naruto.",
        part="Capítulos",
        created_at="2026-11-01T10:00:00Z",
    )
    i2 = _bug(
        base_num + 1,
        "Sushi Manga — Naruto cap 12 erro JSON",
        "Ao chamar GET /api/chapters/12 da obra Naruto retorna JsonDecodingException Unexpected JSON token.",
        part="Capítulos",
        created_at="2026-11-02T10:00:00Z",
        author=author,
    )
    return i1, i2


def test_t4_01_executor_full_happy_path_comment_label_close_and_label_cleanup():
    i1, i2 = _dup_pair(500)
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])
    assert dec.should_auto_close is True

    api = InMemoryGitHubApi([i1, i2])
    res = GuardActionExecutor(api, _cfg(), {500: i1, 501: i2}, matcher=matcher).apply(dec)
    assert res.action_taken == "auto_closed"
    assert res.closed is True
    assert api.issues[501]["state"] == "closed"
    assert api.issues[501]["state_reason"] == "duplicate"
    lbls = {l["name"] for l in api.issues[501]["labels"]}
    assert LABEL_DUPLICATE in lbls
    assert "Em análise" not in lbls
    assert "Problema na extensão" in lbls and "Mangá" in lbls
    assert "#500" in api.comments[501][0]["body"]


def test_t4_02_executor_http_403_on_comment_aborts_before_label_or_close():
    i1, i2 = _dup_pair(502)
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])

    api = InMemoryGitHubApi([i1, i2])
    api.fail_on["create_comment"] = PermissionDeniedError("Forbidden", status=403)
    res = GuardActionExecutor(api, _cfg(), {502: i1, 503: i2}, matcher=matcher).apply(dec)
    assert res.action_taken == "aborted_comment_failed"
    assert res.closed is False
    assert api.issues[503]["state"] == "open"
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[503]["labels"]}


def test_t4_03_executor_http_404_canonical_inaccessible_downgrades():
    i1, i2 = _dup_pair(504)
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])

    api = InMemoryGitHubApi([i1, i2])
    orig_get = api.get_issue

    def get_with_404(num: int):
        if num == 504:
            raise GitHubApiError("Not Found", status=404)
        return orig_get(num)

    api.get_issue = get_with_404  # type: ignore[method-assign]
    res = GuardActionExecutor(api, _cfg(), {504: i1, 505: i2}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert api.issues[505]["state"] == "open"
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[505]["labels"]}


def test_t4_04_executor_http_422_close_failure_rolls_back_label_and_comment():
    i1, i2 = _dup_pair(506)
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])

    api = InMemoryGitHubApi([i1, i2])
    api.fail_on["close_issue"] = GitHubApiError("Validation Failed", status=422)
    res = GuardActionExecutor(api, _cfg(), {506: i1, 507: i2}, matcher=matcher).apply(dec)
    assert res.action_taken == "partial_failure_before_close"
    assert res.closed is False
    assert api.issues[507]["state"] == "open"
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[507]["labels"]}
    assert "commented" in api.comments[507][0]["body"]


def test_t4_05_github_client_422_falls_back_to_not_planned():
    client = GitHubClient("ProjectNox-org/project-nox-requests", token="dummy", sleep_fn=lambda _: None)
    calls: list[dict] = []

    def fake_request(method: str, url: str, payload=None, allow_404=False):
        calls.append({"method": method, "url": url, "payload": payload})
        if len(calls) == 1:
            raise GitHubApiError("Unprocessable Entity", status=422)
        return 200, {}, {"number": 11, "state": "closed", "state_reason": "not_planned"}

    with patch.object(client, "_request", side_effect=fake_request):
        out = client.close_issue(11, state_reason="duplicate")
    assert out["state_reason"] == "not_planned"
    assert len(calls) == 2


def test_t4_06_executor_http_500_on_add_labels_downgrades_and_never_closes():
    i1, i2 = _dup_pair(508)
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])

    api = InMemoryGitHubApi([i1, i2])
    api.fail_on["add_labels"] = GitHubApiError("Internal Server Error", status=500)
    res = GuardActionExecutor(api, _cfg(), {508: i1, 509: i2}, matcher=matcher).apply(dec)
    assert res.action_taken == "partial_failure_before_close"
    assert res.closed is False
    assert api.issues[509]["state"] == "open"
    assert "auto_closed" not in api.comments[509][0]["body"]


def test_t4_07_executor_rate_limit_preserves_issue_and_exits_3_in_cli(tmp_path, capsys):
    i1, i2 = _dup_pair(510)
    api = InMemoryGitHubApi([i1, i2])
    api.fail_on["close_issue"] = RateLimitError("API rate limit exceeded", status=429)
    cache_path = tmp_path / "rl_cache.json"
    rc = run_cli(["--mode", "backfill", "--cache-file", str(cache_path)], client_override=api)
    assert rc == 3
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "incomplete_rate_limited"
    assert api.issues[511]["state"] == "open"
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[511]["labels"]}
    # Rate-limited #511 must NOT be falsely cached as completed
    assert "511" not in json.loads(cache_path.read_text(encoding="utf-8"))["evaluated"]


def test_t4_08_concurrent_human_intervention_label_between_comment_and_label_aborts():
    i1, i2 = _dup_pair(512)
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])

    class HumanAceitoMidRunApi(InMemoryGitHubApi):
        def create_comment(self, issue_number: int, body: str) -> dict:
            res = super().create_comment(issue_number, body)
            if issue_number == 513:
                self.issues[513]["labels"].append({"name": "Aceito"})
            return res

    api = HumanAceitoMidRunApi([i1, i2])
    res = GuardActionExecutor(api, _cfg(), {512: i1, 513: i2}, matcher=matcher).apply(dec)
    assert res.action_taken == "aborted_concurrent_edit_before_label"
    assert res.closed is False
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[513]["labels"]}


def test_t4_09_concurrent_canonical_closed_between_label_and_close_rolls_back():
    i1, i2 = _dup_pair(514)
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])

    class CanonClosedAtBarrierApi(InMemoryGitHubApi):
        def add_labels(self, issue_number: int, labels: list[str]) -> None:
            super().add_labels(issue_number, labels)
            if issue_number == 515:
                self.issues[514]["state"] = "closed"

    api = CanonClosedAtBarrierApi([i1, i2])
    res = GuardActionExecutor(api, _cfg(), {514: i1, 515: i2}, matcher=matcher).apply(dec)
    assert res.action_taken == "aborted_concurrent_edit_before_close"
    assert res.closed is False
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[515]["labels"]}


def test_t4_10_target_edited_after_comment_and_before_label_downgrades():
    i1, i2 = _dup_pair(516)
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])

    class EditedAfterCommentApi(InMemoryGitHubApi):
        def create_comment(self, issue_number: int, body: str) -> dict:
            res = super().create_comment(issue_number, body)
            if issue_number == 517:
                self.issues[517]["body"] = self.issues[517]["body"].replace("Naruto", "One Piece")
            return res

    api = EditedAfterCommentApi([i1, i2])
    res = GuardActionExecutor(api, _cfg(), {516: i1, 517: i2}, matcher=matcher).apply(dec)
    assert res.action_taken == "aborted_concurrent_edit_before_label"
    assert res.closed is False
    assert api.issues[517]["state"] == "open"


def test_t4_11_cache_migrates_from_v140_to_v150_and_blocks_stale_close(tmp_path, capsys):
    assert RULE_ENGINE_VERSION == "1.5.0"
    b200 = _bug(200, "Sushi Manga — Naruto não carrega", "Ao abrir o leitor aparece No pages found.")
    b201 = _bug(201, "Sushi Manga — One Piece não carrega", "Ao abrir o leitor aparece No pages found.", created_at="2026-11-02T10:00:00Z")
    cache_file = tmp_path / "cache_140.json"
    cache_file.write_text(
        json.dumps(
            {
                "rule_version": "1.4.0",
                "last_full_backfill_at": "2026-11-20T00:00:00Z",
                "evaluated": {
                    "201": {
                        "updated_at": b201.updated_at,
                        "fingerprint": parse_issue(b201).content_fingerprint,
                        "catalog_digest": "empty",
                        "auto_close_enabled": True,
                        "decision": "confirmed_duplicate",
                        "canonical_issue": 200,
                        "canonical_state": "open",
                        "action_taken": "auto_closed",
                    }
                },
                "dup_links": {},
                "catalog": {},
            }
        ),
        encoding="utf-8",
    )
    api = InMemoryGitHubApi([b200, b201])
    rc = run_cli(["--mode", "reconcile", "--cache-file", str(cache_file)], client_override=api)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rule_version"] == "1.5.0"
    assert out["effective_mode"] == "backfill"
    assert out["skipped_cached_count"] == 0
    assert api.issues[201]["state"] == "open"
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[201]["labels"]}


def test_t4_12_repeated_execution_strictly_idempotent_after_close():
    i1, i2 = _dup_pair(518)
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])
    api = InMemoryGitHubApi([i1, i2])
    r1 = GuardActionExecutor(api, _cfg(), {518: i1, 519: i2}, matcher=matcher).apply(dec)
    assert r1.action_taken == "auto_closed"
    writes_after = len(api.writes)
    r2 = GuardActionExecutor(api, _cfg(), {518: i1, 519: i2}, matcher=matcher).apply(dec)
    assert r2.action_taken == "noop_idempotent"
    assert len(api.writes) == writes_after
    assert len(api.comments[519]) == 1


def test_t4_13_cli_reconcile_restores_v150_cache_with_zero_api_reprocessing(tmp_path, capsys):
    i1, i2 = _dup_pair(520)
    api = InMemoryGitHubApi([i1, i2])
    cache_file = tmp_path / "state.json"

    rc1 = run_cli(["--mode", "backfill", "--cache-file", str(cache_file)], client_override=api)
    assert rc1 == 0
    out1 = json.loads(capsys.readouterr().out)
    assert out1["rule_version"] == "1.5.0"
    assert api.issues[521]["state"] == "closed"

    api.api_calls_made = 0
    writes_before = len(api.writes)
    rc2 = run_cli(["--mode", "reconcile", "--cache-file", str(cache_file)], client_override=api)
    assert rc2 == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["processed_via_api_count"] == 0
    assert out2["skipped_cached_count"] == 1  # #520 open & cached
    assert len(api.writes) == writes_before


def test_t4_14_backfill_processes_chronologically_and_collapses_chain(tmp_path, capsys):
    i1 = _bug(522, "Sushi Manga — Naruto cap 12 JsonDecodingException", "JsonDecodingException Unexpected JSON token em https://api.sushimanga.com.br/api/chapters/12 na obra Naruto.", part="Capítulos", created_at="2026-11-01T10:00:00Z")
    i2 = _bug(523, "Sushi Manga — Naruto cap 12 erro JSON", "JsonDecodingException Unexpected JSON token em GET /api/chapters/12 da obra Naruto.", part="Capítulos", created_at="2026-11-02T10:00:00Z")
    i3 = _bug(524, "Sushi Manga — Naruto cap 12 JSON inválido", "JsonDecodingException Unexpected JSON token chamando /api/chapters/12 de Naruto.", part="Capítulos", created_at="2026-11-03T10:00:00Z")
    api = InMemoryGitHubApi([i3, i1, i2])
    rc = run_cli(["--mode", "backfill", "--cache-file", str(tmp_path / "bf.json")], client_override=api)
    assert rc == 0
    _ = capsys.readouterr()
    assert api.issues[522]["state"] == "open"
    assert api.issues[523]["state"] == "closed"
    assert api.issues[524]["state"] == "closed"
    assert "#522" in api.comments[524][0]["body"]


def test_t4_15_live_catalog_removed_aborts_standalone_creation_closure():
    req = _ne(525, "HentaiHaven", "https://hentaihaven.xxx/", ext_type="Anime", lang="Inglês", created_at="2026-11-20T10:00:00Z")
    cat = build_extension_catalog(
        {
            "anime": [
                {
                    "name": "HentaiHaven",
                    "pkg": "eu.hh",
                    "lang": "en",
                    "version": "16.15",
                    "sources": [{"name": "HentaiHaven", "lang": "en", "baseUrl": "https://hentaihaven.xxx"}],
                }
            ],
            "manga": [],
        }
    )
    matcher = DuplicateMatcher(parse_corpus([req]), _cfg(), catalog=cat)
    dec = matcher.evaluate(parse_corpus([req])[0])
    assert dec.match_kind == "already_fulfilled_creation"
    assert dec.should_auto_close is True

    api = InMemoryGitHubApi([req], catalog_payload={"anime": [], "manga": []})
    res = GuardActionExecutor(api, _cfg(), {525: req}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert api.issues[525]["state"] == "open"
    assert LABEL_DONE not in {l["name"] for l in api.issues[525]["labels"]}


def test_t4_16_fingerprint_includes_destination_subdomain_and_reproduction_changes():
    b_repro1 = _bug(526, "Sushi Manga — Erro", "Erro no leitor.", repro="Abrir Naruto capítulo 5.")
    b_repro2 = _bug(526, "Sushi Manga — Erro", "Erro no leitor.", repro="Abrir Naruto capítulo 9.")
    assert parse_issue(b_repro1).content_fingerprint != parse_issue(b_repro2).content_fingerprint

    dc_pt = _dc(527, "Sushi", "https://sushi.old", "https://pt.sushi.com")
    dc_en = _dc(527, "Sushi", "https://sushi.old", "https://en.sushi.com")
    assert parse_issue(dc_pt).content_fingerprint != parse_issue(dc_en).content_fingerprint


def test_t4_17_misfiled_bug_under_new_extension_preserved_not_fulfilled_by_catalog():
    misfiled = _ne(
        528,
        "HentaiHaven",
        "https://hentaihaven.xxx/",
        ext_type="Anime",
        lang="Inglês",
        obs="A extensão parou de funcionar e dá erro HTTP 403 ao carregar vídeos.",
        created_at="2026-11-20T10:00:00Z",
    )
    cat = build_extension_catalog(
        {
            "anime": [
                {
                    "name": "HentaiHaven",
                    "pkg": "eu.hh",
                    "lang": "en",
                    "version": "16.15",
                    "sources": [{"name": "HentaiHaven", "lang": "en", "baseUrl": "https://hentaihaven.xxx"}],
                }
            ],
            "manga": [],
        }
    )
    dec = DuplicateMatcher(parse_corpus([misfiled]), _cfg(), catalog=cat).evaluate(parse_corpus([misfiled])[0])
    assert dec.should_auto_close is False
    assert dec.classification == "no_match"
    assert dec.inferred_category == "bug"
    assert dec.category_mismatch is True
    comment = render_comment_body(dec, "commented", {528: misfiled})
    assert "Problema na extensão" in comment
