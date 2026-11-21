# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Comprehensive v1.4.0 audit regression suite (50 scenarios across Blocks A, B, C, D)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.duplicate_guard.actions import GuardActionExecutor  # noqa: E402
from scripts.duplicate_guard.cli import parse_corpus, run_cli  # noqa: E402
from scripts.duplicate_guard.config import load_config  # noqa: E402
from scripts.duplicate_guard.github_client import (  # noqa: E402
    GitHubApiError,
    PermissionDeniedError,
    RateLimitError,
)
from scripts.duplicate_guard.matcher import DuplicateMatcher  # noqa: E402
from scripts.duplicate_guard.models import (  # noqa: E402
    LABEL_DONE,
    LABEL_DUPLICATE,
    RULE_ENGINE_VERSION,
    ExtensionCatalog,
)
from scripts.duplicate_guard.parser import build_extension_catalog, parse_issue  # noqa: E402
from tests.test_duplicate_guard import (  # noqa: E402
    InMemoryGitHubApi,
    evaluate_pair,
    load_real_fixtures,
    make_raw,
)

WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"


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
    created_at: str = "2026-11-01T10:00:00Z",
    state: str = "open",
    state_reason: str | None = None,
    labels: list[str] | None = None,
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
    return make_raw(
        num,
        f"[Problema na extensão]: {ext} — {title}",
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
# Block A — Shared Hostname & Generic Exception Isolation (14 tests)
# ---------------------------------------------------------------------------

def test_a01_sec32_case_a_same_host_cover_vs_metadata():
    i200 = _bug(
        200,
        "Capas não carregam",
        "As capas das obras em https://sushimanga.com.br não aparecem.",
        part="Leitor / Imagens",
        created_at="2026-11-01T10:00:00Z",
    )
    i201 = _bug(
        201,
        "Sinopse e título errados",
        "A sinopse e o título das obras em https://sushimanga.com.br vêm errados.",
        part="Outro",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i200, i201], 201, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_a02_sec32_case_b_same_host_cloudflare_vs_no_pages():
    i202 = _bug(
        202,
        "Cloudflare nos capítulos",
        "Ao abrir capítulo de https://sushimanga.com.br da obra A dá Cloudflare HTTP 403.",
        part="Capítulos",
        created_at="2026-11-01T10:00:00Z",
    )
    i203 = _bug(
        203,
        "Sem imagens no leitor",
        "Erro No pages found ao abrir capítulo de https://sushimanga.com.br no leitor.",
        part="Leitor / Imagens",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i202, i203], 203, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_a03_sec32_case_c_same_host_timeout_vs_json_decode():
    i204 = _bug(
        204,
        "Timeout na API",
        "Timeout ao carregar https://api.sushimanga.com.br/chapters",
        part="Capítulos",
        created_at="2026-11-01T10:00:00Z",
    )
    i205 = _bug(
        205,
        "JSON inválido na busca",
        "Resposta JSON inválida em https://api.sushimanga.com.br/search",
        part="Busca / Filtros",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i204, i205], 205, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_a04_sec32_case_d_generic_ioexception_chapters_vs_avatar():
    i206 = _bug(
        206,
        "IOException capítulos",
        "java.io.IOException ao baixar páginas do capítulo.",
        part="Leitor / Imagens",
        created_at="2026-11-01T10:00:00Z",
    )
    i207 = _bug(
        207,
        "IOException avatar",
        "java.io.IOException ao carregar avatar do perfil.",
        part="Outro",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i206, i207], 207, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_a05_sec32_case_e_generic_runtime_exception_same_part_never_confirmed():
    i208 = _bug(
        208,
        "RuntimeException filtro gênero",
        "RuntimeException ao aplicar filtro de gênero na busca.",
        part="Busca / Filtros",
        created_at="2026-11-01T10:00:00Z",
    )
    i209 = _bug(
        209,
        "RuntimeException ordenação alfabética",
        "RuntimeException ao mudar ordenação alfabética da busca.",
        part="Busca / Filtros",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i208, i209], 209, _cfg())
    assert dec.classification != "confirmed_duplicate"
    assert dec.should_auto_close is False


def test_a06_generic_exceptions_map_to_gexc_not_exc():
    sample = _bug(
        210,
        "Exceções variadas",
        "Falha com HttpException, SocketTimeoutException, ConnectException, RuntimeException e Exception.",
    )
    parsed = parse_issue(sample)
    assert "gexc:ioexception" not in parsed.technical_tokens  # sanity not falsely added
    for name in ("httpexception", "sockettimeoutexception", "connectexception", "runtimeexception", "exception"):
        assert f"gexc:{name}" in parsed.technical_tokens
        assert f"exc:{name}" not in parsed.technical_tokens


def test_a07_same_host_same_part_different_symptoms_never_confirmed():
    i211 = _bug(
        211,
        "Capas borradas",
        "As capas em https://sushimanga.com.br carregam borradas em baixa resolução.",
        part="Leitor / Imagens",
        created_at="2026-11-01T10:00:00Z",
    )
    i212 = _bug(
        212,
        "Erro de SSL no leitor",
        "Erro SSLHandshakeException ao abrir páginas em https://sushimanga.com.br.",
        part="Leitor / Imagens",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i211, i212], 212, _cfg())
    assert dec.classification != "confirmed_duplicate"
    assert dec.should_auto_close is False


def test_a08_shared_host_with_matching_nxdomain_confirmed():
    i213 = _bug(
        213,
        "NXDOMAIN DNS",
        "Unable to resolve host apiv2.taimu.com: No address associated with hostname.",
        part="Outro",
        created_at="2026-11-01T10:00:00Z",
    )
    i214 = _bug(
        214,
        "UnknownHostException DNS",
        "UnknownHostException: Unable to resolve host \"apiv2.taimu.com\" no endereço.",
        part="Outro",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i213, i214], 214, _cfg())
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True


def test_a09_nxdomain_on_different_subdomains_never_confirmed():
    i215 = _bug(
        215,
        "NXDOMAIN CDN",
        "Unable to resolve host cdn1.taimu.com: No address associated with hostname.",
        part="Leitor / Imagens",
        created_at="2026-11-01T10:00:00Z",
    )
    i216 = _bug(
        216,
        "NXDOMAIN API",
        "Unable to resolve host api.taimu.com: No address associated with hostname.",
        part="Leitor / Imagens",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i215, i216], 216, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_a10_unilateral_specific_error_signature_blocks_auto_close():
    i217 = _bug(
        217,
        "JsonDecodingException na busca",
        "JsonDecodingException ao decodificar JSON de pesquisa na busca.",
        part="Busca / Filtros",
        created_at="2026-11-01T10:00:00Z",
    )
    i218 = _bug(
        218,
        "A busca não funciona",
        "Quando pesquiso qualquer termo na busca os resultados não carregam.",
        part="Busca / Filtros",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i217, i218], 218, _cfg())
    assert dec.should_auto_close is False
    assert dec.classification != "confirmed_duplicate"


def test_a11_specific_exception_without_supporting_tokens_or_endpoint_never_confirmed():
    i219 = _bug(
        219,
        "JsonSyntaxException filtros",
        "JsonSyntaxException nos filtros.",
        part="Busca / Filtros",
        created_at="2026-11-01T10:00:00Z",
    )
    i220 = _bug(
        220,
        "JsonSyntaxException capítulos",
        "JsonSyntaxException ao listar.",
        part="Capítulos",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i219, i220], 220, _cfg())
    assert dec.should_auto_close is False


def test_a12_specific_exception_with_shared_endpoint_and_tokens_confirmed():
    i221 = _bug(
        221,
        "JsonDecodingException /api/v2/chapters",
        "JsonDecodingException Unexpected JSON token no endpoint /api/v2/chapters ao carregar páginas.",
        part="Capítulos",
        created_at="2026-11-01T10:00:00Z",
    )
    i222 = _bug(
        222,
        "Erro JsonDecodingException em /api/v2/chapters",
        "Ao abrir capítulos ocorre JsonDecodingException Unexpected JSON token em /api/v2/chapters.",
        part="Capítulos",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i221, i222], 222, _cfg())
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True


def test_a13_generic_video_playback_not_strong_signature():
    i223 = _bug(
        223,
        "Vídeo travando no buffer",
        "Player de vídeo não reproduz e fica travando no carregamento.",
        ext="AnimeTop",
        ext_type="Anime",
        part="Vídeo / Player",
        created_at="2026-11-01T10:00:00Z",
    )
    i224 = _bug(
        224,
        "Áudio dessincronizado no vídeo",
        "Player reproduz o vídeo mas o áudio fica atrasado e sem som.",
        ext="AnimeTop",
        ext_type="Anime",
        part="Vídeo / Player",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i223, i224], 224, _cfg())
    assert dec.should_auto_close is False
    assert dec.classification != "confirmed_duplicate"


def test_a14_host_token_suppressed_without_nxdomain():
    i225 = _bug(
        225,
        "Erro genérico em URL",
        "Erro desconhecido ao acessar https://sushimanga.com.br/obra/teste.",
    )
    parsed = parse_issue(i225)
    assert "host:sushimanga.com.br" not in parsed.technical_tokens


# ---------------------------------------------------------------------------
# Block B — Works, Chapters, Episodes, Endpoints & Unilateral Scope (14 tests)
# ---------------------------------------------------------------------------

def test_b01_sec42_scen1_distinct_works_naruto_vs_one_piece():
    i300 = _bug(
        300,
        "No pages found Naruto",
        "Erro No pages found no capítulo 12 de Naruto.",
        part="Leitor / Imagens",
        created_at="2026-11-01T10:00:00Z",
    )
    i301 = _bug(
        301,
        "No pages found One Piece",
        "Erro No pages found no capítulo 12 de One Piece.",
        part="Leitor / Imagens",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i300, i301], 301, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_b02_sec42_scen2_distinct_chapters_12_vs_18():
    i302 = _bug(
        302,
        "Naruto cap 12",
        "No pages found no capítulo 12 de Naruto.",
        part="Leitor / Imagens",
        created_at="2026-11-01T10:00:00Z",
    )
    i303 = _bug(
        303,
        "Naruto cap 18",
        "No pages found no capítulo 18 de Naruto.",
        part="Leitor / Imagens",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i302, i303], 303, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_b03_sec42_scen3_specific_chapter_vs_generic_vague():
    i304 = _bug(
        304,
        "Naruto cap 12 No pages found",
        "Erro No pages found no capítulo 12 de Naruto.",
        part="Leitor / Imagens",
        created_at="2026-11-01T10:00:00Z",
    )
    i305 = _bug(
        305,
        "Não carrega",
        "Não carrega nada ao tentar ler.",
        part="Leitor / Imagens",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i304, i305], 305, _cfg())
    assert dec.should_auto_close is False
    assert dec.classification != "confirmed_duplicate"


def test_b04_sec42_scen4_distinct_episodes_5_vs_9():
    i306 = _bug(
        306,
        "Episódio 5",
        "Player falha ao reproduzir o episódio 5.",
        ext="Anime X",
        ext_type="Anime",
        part="Vídeo / Player",
        created_at="2026-11-01T10:00:00Z",
    )
    i307 = _bug(
        307,
        "Episódio 9",
        "Player falha ao reproduzir o episódio 9.",
        ext="Anime X",
        ext_type="Anime",
        part="Vídeo / Player",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i306, i307], 307, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_b05_sec42_scen5_distinct_api_endpoints():
    i308 = _bug(
        308,
        "Endpoint /api/search HTTP 500",
        "Endpoint /api/search retorna HTTP 500.",
        part="Outro",
        created_at="2026-11-01T10:00:00Z",
    )
    i309 = _bug(
        309,
        "Endpoint /api/chapters/12 HTTP 500",
        "Endpoint /api/chapters/12 retorna HTTP 500.",
        part="Outro",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i308, i309], 309, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_b06_sec42_scen6_identical_work_chapter_and_signature_confirmed():
    i310 = _bug(
        310,
        "Naruto cap 12 No pages found",
        "Erro No pages found no capítulo 12 de Naruto ao abrir imagens no leitor.",
        part="Leitor / Imagens",
        created_at="2026-11-01T10:00:00Z",
    )
    i311 = _bug(
        311,
        "No pages found capítulo 12 Naruto",
        "Ao carregar o capítulo 12 da obra Naruto aparece No pages found.",
        part="Leitor / Imagens",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i310, i311], 311, _cfg())
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True


def test_b07_work_hint_variants_da_obra_do_anime():
    i312 = _bug(
        312,
        "Falha Solo Leveling",
        "Erro No pages found ao abrir capítulo da obra Solo Leveling.",
        created_at="2026-11-01T10:00:00Z",
    )
    i313 = _bug(
        313,
        "Falha Chainsaw Man",
        "Erro No pages found ao abrir capítulo da obra Chainsaw Man.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i312, i313], 313, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_b08_unilateral_work_hint_never_auto_closes():
    i314 = _bug(
        314,
        "No pages found Naruto",
        "Erro No pages found na obra Naruto ao abrir o leitor.",
        created_at="2026-11-01T10:00:00Z",
    )
    i315 = _bug(
        315,
        "No pages found no leitor",
        "Erro No pages found em todas as obras ao tentar abrir.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i314, i315], 315, _cfg())
    assert dec.should_auto_close is False
    assert dec.classification != "confirmed_duplicate"


def test_b09_unilateral_chapter_number_never_auto_closes():
    i316 = _bug(
        316,
        "Naruto cap 12",
        "Erro No pages found no capítulo 12 de Naruto.",
        created_at="2026-11-01T10:00:00Z",
    )
    i317 = _bug(
        317,
        "Naruto sem páginas",
        "Erro No pages found na obra Naruto ao tentar ler.",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i316, i317], 317, _cfg())
    assert dec.should_auto_close is False


def test_b10_unilateral_episode_number_never_auto_closes():
    i318 = _bug(
        318,
        "Episódio 5 falha JsonDecodingException",
        "JsonDecodingException no episódio 5 ao extrair stream do vídeo.",
        ext="Anime X",
        ext_type="Anime",
        part="Vídeo / Player",
        created_at="2026-11-01T10:00:00Z",
    )
    i319 = _bug(
        319,
        "Falha JsonDecodingException no player",
        "JsonDecodingException ao tentar extrair stream no player.",
        ext="Anime X",
        ext_type="Anime",
        part="Vídeo / Player",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i318, i319], 319, _cfg())
    assert dec.should_auto_close is False


def test_b11_unilateral_api_endpoint_never_auto_closes():
    i320 = _bug(
        320,
        "Erro /graphql/chapters",
        "JsonDecodingException ao consultar /graphql/chapters.",
        part="Capítulos",
        created_at="2026-11-01T10:00:00Z",
    )
    i321 = _bug(
        321,
        "Erro de parsing na extensão",
        "JsonDecodingException ao carregar lista de capítulos.",
        part="Capítulos",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i320, i321], 321, _cfg())
    assert dec.should_auto_close is False


def test_b12_decimal_chapter_numbers_isolated():
    i322 = _bug(
        322,
        "No pages found cap. 12.5",
        "Erro No pages found no cap. 12.5 da obra Hunter.",
        part="Leitor / Imagens",
        created_at="2026-11-01T10:00:00Z",
    )
    i323 = _bug(
        323,
        "No pages found cap. 12",
        "Erro No pages found no cap. 12 da obra Hunter.",
        part="Leitor / Imagens",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i322, i323], 323, _cfg())
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_b13_author_comment_adding_divergent_chapter_blocks_closure():
    orig = _bug(
        324,
        "No pages found leitor",
        "Erro No pages found no capítulo 12 de Naruto ao abrir leitor.",
        part="Leitor / Imagens",
        created_at="2026-11-01T10:00:00Z",
    )
    cand = _bug(
        325,
        "No pages found leitor",
        "Erro No pages found no capítulo 12 de Naruto ao abrir leitor.",
        part="Leitor / Imagens",
        created_at="2026-11-02T10:00:00Z",
    )
    comments = {
        325: [
            {
                "id": 8801,
                "user": {"login": cand.author, "type": "User"},
                "author_association": "NONE",
                "body": "Atualizando: também falha no capítulo 99 de outra obra!",
            }
        ]
    }
    parsed = parse_corpus([orig, cand])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])
    assert dec.should_auto_close is True

    api = InMemoryGitHubApi([orig, cand], initial_comments=comments)
    res = GuardActionExecutor(api, _cfg(), {324: orig, 325: cand}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert api.issues[325]["state"] == "open"
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[325]["labels"]}


def test_b14_matching_episode_and_endpoint_with_specific_exception_confirmed():
    i326 = _bug(
        326,
        "Ep 14 JsonDecodingException",
        "JsonDecodingException em /api/episodes/14 ao abrir o episódio 14.",
        ext="Anime X",
        ext_type="Anime",
        part="Vídeo / Player",
        created_at="2026-11-01T10:00:00Z",
    )
    i327 = _bug(
        327,
        "Episódio 14 falha /api/episodes/14",
        "Erro JsonDecodingException no endpoint /api/episodes/14 tentando assistir episódio 14.",
        ext="Anime X",
        ext_type="Anime",
        part="Vídeo / Player",
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i326, i327], 327, _cfg())
    assert dec.classification == "confirmed_duplicate"
    assert dec.should_auto_close is True


# ---------------------------------------------------------------------------
# Block C — Live Catalog Verification, Cache Digest & Failures (11 tests)
# ---------------------------------------------------------------------------

def _hh_payload(lang: str = "en") -> dict[str, list[dict]]:
    return {
        "anime": [
            {
                "name": "Aniyomi: HentaiHaven",
                "pkg": f"eu.kanade.tachiyomi.animeextension.{lang}.hentaihaven",
                "apk": "aniyomi.hh.apk",
                "lang": lang,
                "version": "16.15",
                "sources": [{"name": "HentaiHaven", "lang": lang, "baseUrl": "https://hentaihaven.xxx"}],
            }
        ],
        "manga": [],
    }


def _hh_req(num: int = 401, lang: str = "Inglês"):
    return _ne(num, "HentaiHaven", "https://hentaihaven.xxx/", ext_type="Anime", lang=lang, created_at="2026-11-15T10:00:00Z")


def test_c01_live_catalog_extension_removed_before_close_aborts():
    req = _hh_req(401, "Inglês")
    cat = build_extension_catalog(_hh_payload("en"))
    matcher = DuplicateMatcher(parse_corpus([req]), _cfg(), catalog=cat)
    dec = matcher.evaluate(parse_corpus([req])[0])
    assert dec.should_auto_close is True

    # Live catalog is now empty!
    api = InMemoryGitHubApi([req], catalog_payload={"anime": [], "manga": []})
    res = GuardActionExecutor(api, _cfg(), {401: req}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert api.issues[401]["state"] == "open"
    assert LABEL_DONE not in {l["name"] for l in api.issues[401]["labels"]}
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[401]["labels"]}


def test_c02_live_catalog_language_changed_to_mismatch_aborts():
    req = _hh_req(402, "Inglês")
    cat = build_extension_catalog(_hh_payload("en"))
    matcher = DuplicateMatcher(parse_corpus([req]), _cfg(), catalog=cat)
    dec = matcher.evaluate(parse_corpus([req])[0])

    api = InMemoryGitHubApi([req], catalog_payload=_hh_payload("pt-BR"))
    res = GuardActionExecutor(api, _cfg(), {402: req}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert api.issues[402]["state"] == "open"


def test_c03_live_catalog_http_404_fails_closed():
    req = _hh_req(403, "Inglês")
    cat = build_extension_catalog(_hh_payload("en"))
    matcher = DuplicateMatcher(parse_corpus([req]), _cfg(), catalog=cat)
    dec = matcher.evaluate(parse_corpus([req])[0])

    api = InMemoryGitHubApi([req])
    api.fail_on["fetch_catalog_json"] = GitHubApiError("Not Found", status=404)
    res = GuardActionExecutor(api, _cfg(), {403: req}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert api.issues[403]["state"] == "open"


def test_c04_live_catalog_http_403_permission_denied_fails_closed():
    req = _hh_req(404, "Inglês")
    cat = build_extension_catalog(_hh_payload("en"))
    matcher = DuplicateMatcher(parse_corpus([req]), _cfg(), catalog=cat)
    dec = matcher.evaluate(parse_corpus([req])[0])

    api = InMemoryGitHubApi([req])
    api.fail_on["fetch_catalog_json"] = PermissionDeniedError("Forbidden", status=403)
    res = GuardActionExecutor(api, _cfg(), {404: req}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert api.issues[404]["state"] == "open"


def test_c05_live_catalog_rate_limit_blocks_closure():
    req = _hh_req(405, "Inglês")
    cat = build_extension_catalog(_hh_payload("en"))
    matcher = DuplicateMatcher(parse_corpus([req]), _cfg(), catalog=cat)
    dec = matcher.evaluate(parse_corpus([req])[0])

    api = InMemoryGitHubApi([req])
    api.fail_on["fetch_catalog_json"] = RateLimitError("rate limited", status=429)
    res = GuardActionExecutor(api, _cfg(), {405: req}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert "rate_limit" in res.action_taken or "rate" in (res.skipped_reason or "").lower()
    assert api.issues[405]["state"] == "open"


def test_c06_live_catalog_disappears_at_pre_close_barrier_rolls_back_label():
    req = _hh_req(406, "Inglês")
    cat = build_extension_catalog(_hh_payload("en"))
    matcher = DuplicateMatcher(parse_corpus([req]), _cfg(), catalog=cat)
    dec = matcher.evaluate(parse_corpus([req])[0])

    class DisappearingCatalogApi(InMemoryGitHubApi):
        def __init__(self, issues, *, catalog_payload=None):
            super().__init__(issues, catalog_payload=catalog_payload)
            self.catalog_fetches = 0

        def fetch_catalog_json(self, repo_slug: str, path: str = "index.min.json"):
            self.catalog_fetches += 1
            if self.catalog_fetches >= 2:
                # Extension was removed between initial revalidation and pre-close barrier!
                return []
            return super().fetch_catalog_json(repo_slug, path)

    api = DisappearingCatalogApi([req], catalog_payload=_hh_payload("en"))
    res = GuardActionExecutor(api, _cfg(), {406: req}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert api.issues[406]["state"] == "open"
    final_labels = {l["name"] for l in api.issues[406]["labels"]}
    assert LABEL_DONE not in final_labels
    assert LABEL_DUPLICATE not in final_labels


def test_c07_live_catalog_all_requested_vs_partial_en_es_blocks_close():
    req = _hh_req(407, "Todos os idiomas")
    payload = {
        "anime": [
            {
                "name": "Aniyomi: HentaiHaven",
                "pkg": "eu.kanade.tachiyomi.animeextension.all.hentaihaven",
                "lang": "en",
                "version": "16.15",
                "sources": [
                    {"name": "HentaiHaven", "lang": "en", "baseUrl": "https://hentaihaven.xxx"},
                    {"name": "HentaiHaven", "lang": "es", "baseUrl": "https://hentaihaven.xxx"},
                ],
            }
        ],
        "manga": [],
    }
    cat = build_extension_catalog(payload)
    dec = DuplicateMatcher(parse_corpus([req]), _cfg(), catalog=cat).evaluate(parse_corpus([req])[0])
    assert dec.should_auto_close is False
    assert dec.classification == "no_match"


def test_c08_live_catalog_incomplete_metadata_never_closes():
    req = _hh_req(408, "Inglês")
    bad_payload = {
        "anime": [
            {
                "name": "Aniyomi: HentaiHaven",
                "pkg": "",
                "lang": "",
                "version": "16.15",
                "sources": [{"name": "HentaiHaven", "lang": "", "baseUrl": "https://hentaihaven.xxx"}],
            }
        ],
        "manga": [],
    }
    cat = build_extension_catalog(bad_payload)
    matcher = DuplicateMatcher(parse_corpus([req]), _cfg(), catalog=cat)
    dec = matcher.evaluate(parse_corpus([req])[0])
    assert dec.should_auto_close is False

    # Even if matcher had a decision forced, Executor fails closed on incomplete live metadata
    forced_cat = build_extension_catalog(_hh_payload("en"))
    forced_matcher = DuplicateMatcher(parse_corpus([req]), _cfg(), catalog=forced_cat)
    forced_dec = forced_matcher.evaluate(parse_corpus([req])[0])
    api = InMemoryGitHubApi([req], catalog_payload=bad_payload)
    res = GuardActionExecutor(api, _cfg(), {408: req}, matcher=forced_matcher).apply(forced_dec)
    assert res.closed is False
    assert api.issues[408]["state"] == "open"


def test_c09_live_catalog_ambiguous_multiple_packages_blocks_close():
    req = _hh_req(409, "Inglês")
    ambiguous_payload = {
        "anime": [
            {
                "name": "Aniyomi: HentaiHaven",
                "pkg": "eu.pkg.one",
                "lang": "en",
                "version": "1.0",
                "sources": [{"name": "HentaiHaven", "lang": "en", "baseUrl": "https://hentaihaven.xxx"}],
            },
            {
                "name": "Aniyomi: HentaiHaven",
                "pkg": "eu.pkg.two",
                "lang": "en",
                "version": "2.0",
                "sources": [{"name": "HentaiHaven", "lang": "en", "baseUrl": "https://hentaihaven.xxx"}],
            },
        ],
        "manga": [],
    }
    cat = build_extension_catalog(ambiguous_payload)
    matcher = DuplicateMatcher(parse_corpus([req]), _cfg(), catalog=cat)
    dec = matcher.evaluate(parse_corpus([req])[0])
    assert dec.should_auto_close is False

    # Also test Executor barrier if snapshot was unique but live catalog gained a second package
    forced_cat = build_extension_catalog(_hh_payload("en"))
    forced_matcher = DuplicateMatcher(parse_corpus([req]), _cfg(), catalog=forced_cat)
    forced_dec = forced_matcher.evaluate(parse_corpus([req])[0])
    api = InMemoryGitHubApi([req], catalog_payload=ambiguous_payload)
    res = GuardActionExecutor(api, _cfg(), {409: req}, matcher=forced_matcher).apply(forced_dec)
    assert res.closed is False


def test_c10_live_catalog_client_without_catalog_method_fails_closed():
    req = _hh_req(410, "Inglês")
    cat = build_extension_catalog(_hh_payload("en"))
    matcher = DuplicateMatcher(parse_corpus([req]), _cfg(), catalog=cat)
    dec = matcher.evaluate(parse_corpus([req])[0])
    assert dec.should_auto_close is True

    api = InMemoryGitHubApi([req])

    class LegacyClientNoCatalog:
        def __getattr__(self, name: str):
            if name in ("fetch_catalog_json", "list_catalog_extensions"):
                raise AttributeError(name)
            return getattr(api, name)

    res = GuardActionExecutor(LegacyClientNoCatalog(), _cfg(), {410: req}, matcher=matcher).apply(dec)  # type: ignore[arg-type]
    assert res.closed is False
    assert api.issues[410]["state"] == "open"


def test_c11_cli_cache_invalidates_new_extension_when_catalog_digest_changes(tmp_path, capsys):
    # Old open New Extension issue (>60 days old so lookback alone wouldn't revisit it)
    old_open = make_raw(
        411,
        "[Nova extensão]: HentaiHaven",
        "### Tipo de extensão\nAnime\n### Nome da fonte\nHentaiHaven\n### URL do site\nhttps://hentaihaven.xxx/\n### Idioma\nInglês\n",
        labels=["Nova extensão", "Anime", "Em análise"],
        created_at="2026-01-01T10:00:00Z",
        updated_at="2026-01-01T10:00:00Z",
    )
    cache_file = tmp_path / "catalog_digest_cache.json"

    # Run 1 with empty catalog -> cached with empty catalog_digest
    api1 = InMemoryGitHubApi([old_open], catalog_payload={"anime": [], "manga": []})
    rc1 = run_cli(["--mode", "backfill", "--cache-file", str(cache_file)], client_override=api1)
    assert rc1 == 0
    _ = capsys.readouterr()
    assert api1.issues[411]["state"] == "open"

    # Simulate catalog TTL expiry (>6h ago) + HentaiHaven published in catalog, WITHOUT editing issue #411!
    persisted = json.loads(cache_file.read_text(encoding="utf-8"))
    persisted["catalog"]["fetched_at"] = "2020-01-01T00:00:00Z"
    cache_file.write_text(json.dumps(persisted, ensure_ascii=False, indent=2), encoding="utf-8")

    api2 = InMemoryGitHubApi([old_open], catalog_payload=_hh_payload("en"))
    rc2 = run_cli(["--mode", "reconcile", "--cache-file", str(cache_file)], client_override=api2)
    assert rc2 == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["skipped_cached_count"] == 0
    assert out2["catalog_digest"] != ""
    assert api2.issues[411]["state"] == "closed"
    assert LABEL_DONE in {l["name"] for l in api2.issues[411]["labels"]}


# ---------------------------------------------------------------------------
# Block D — 5-Category Hardening, Concurrency & Counterfactuals (11 tests)
# ---------------------------------------------------------------------------

def test_d01_rule_engine_version_1_4_0_invalidates_1_3_0_cache(tmp_path, capsys):
    assert RULE_ENGINE_VERSION >= "1.4.0"
    i1 = _bug(501, "No pages found", "Erro No pages found no capítulo 1 de Naruto.")
    cache_file = tmp_path / "cache_130.json"
    cache_file.write_text(
        json.dumps(
            {
                "rule_version": "1.3.0",
                "last_full_backfill_at": "2026-11-10T00:00:00Z",
                "evaluated": {
                    "501": {
                        "updated_at": i1.updated_at,
                        "fingerprint": "stale",
                        "auto_close_enabled": True,
                        "decision": "no_match",
                        "canonical_issue": None,
                        "canonical_state": None,
                        "action_taken": "noop",
                    }
                },
                "dup_links": {},
                "catalog": {},
            }
        ),
        encoding="utf-8",
    )
    api = InMemoryGitHubApi([i1])
    rc = run_cli(["--mode", "reconcile", "--cache-file", str(cache_file)], client_override=api)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rule_version"] == RULE_ENGINE_VERSION
    assert out["effective_mode"] == "backfill"
    assert out["skipped_cached_count"] == 0


def test_d02_hardened_name_compatibility_blocks_short_or_moderate_dice_collisions():
    cat = build_extension_catalog(
        {
            "manga": [
                {
                    "name": "MangaPlay",
                    "pkg": "eu.mangaplay",
                    "lang": "pt-BR",
                    "version": "1.0",
                    "sources": [{"name": "MangaPlay", "lang": "pt-BR", "baseUrl": "https://mangaplay.example"}],
                },
                {
                    "name": "Kone",
                    "pkg": "eu.kone",
                    "lang": "pt-BR",
                    "version": "1.0",
                    "sources": [{"name": "Kone", "lang": "pt-BR", "baseUrl": "https://kone.example"}],
                },
                {
                    "name": "MangaLivre",
                    "pkg": "eu.mangalivre",
                    "lang": "pt-BR",
                    "version": "1.0",
                    "sources": [{"name": "MangaLivre", "lang": "pt-BR", "baseUrl": "https://mangalivre.example"}],
                },
            ],
            "anime": [],
        }
    )
    t_plus = parse_issue(_ne(520, "MangaPlus", "https://mangaplay.example"))
    t_neko = parse_issue(_ne(521, "Neko", "https://kone.example"))
    t_livre = parse_issue(_ne(522, "Manga Livre", "https://mangalivre.example"))
    t_empty = parse_issue(_ne(523, "", "https://mangalivre.example"))

    assert cat.find_matching_extensions(t_plus) == ()
    assert cat.find_matching_extensions(t_neko) == ()
    assert len(cat.find_matching_extensions(t_livre)) == 1
    assert cat.find_matching_extensions(t_empty) == ()


def test_d03_new_extension_missing_declared_language_never_closes():
    req_no_lang = _ne(
        503,
        "Bakai",
        "###",
        lang="_No response_",
        created_at="2026-11-10T10:00:00Z",
    )
    # Valid URL, missing language
    req_no_lang = make_raw(
        503,
        "[Nova extensão]: Bakai",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nBakai\n### URL do site\nhttps://bakai.org/\n### Idioma\n_No response_\n",
        labels=["Nova extensão", "Mangá"],
        created_at="2026-11-10T10:00:00Z",
    )
    cat = build_extension_catalog(
        {
            "manga": [
                {
                    "name": "Bakai",
                    "pkg": "eu.bakai",
                    "lang": "pt-BR",
                    "version": "1.0",
                    "sources": [{"name": "Bakai", "lang": "pt-BR", "baseUrl": "https://bakai.org"}],
                }
            ],
            "anime": [],
        }
    )
    dec = DuplicateMatcher(parse_corpus([req_no_lang]), _cfg(), catalog=cat).evaluate(parse_corpus([req_no_lang])[0])
    assert dec.should_auto_close is False


def test_d04_new_extension_all_vs_single_lang_never_auto_closes():
    all_req = _ne(504, "MultiScan", "https://multiscan.example/", lang="Todos os idiomas", created_at="2026-11-01T10:00:00Z")
    pt_req = _ne(505, "MultiScan", "https://multiscan.example/", lang="Português (Brasil)", created_at="2026-11-02T10:00:00Z")
    assert evaluate_pair([all_req, pt_req], 505, _cfg()).should_auto_close is False
    assert evaluate_pair([pt_req, all_req], 505, _cfg()).should_auto_close is False


def test_d05_domain_change_same_old_and_new_url_never_auto_closes():
    body = "### Tipo de extensão\nMangá\n### Nome da extensão\nSameUrlScan\n### URL antiga\nhttps://sameurl.com\n### URL nova\nhttps://sameurl.com/\n"
    d1 = make_raw(506, "[Mudança de domínio]: SameUrlScan", body, labels=["Mudança de domínio", "Mangá"], created_at="2026-11-01T10:00:00Z")
    d2 = make_raw(507, "[Mudança de domínio]: SameUrlScan", body, labels=["Mudança de domínio", "Mangá"], created_at="2026-11-02T10:00:00Z")
    assert evaluate_pair([d1, d2], 507, _cfg()).should_auto_close is False


def test_d06_dead_source_qualified_weeks_instability_never_confirmed():
    ds_perm = make_raw(
        508,
        "[Fonte indisponível]: DeadScan",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nDeadScan\n### O que aconteceu?\nO site fechou definitivamente após comunicado oficial da equipe.\n",
        labels=["Fonte indisponível", "Mangá"],
        created_at="2026-11-01T10:00:00Z",
    )
    ds_temp = make_raw(
        509,
        "[Fonte indisponível]: DeadScan",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nDeadScan\n### O que aconteceu?\nFaz semanas que está dando HTTP 403 e instabilidade temporária.\n",
        labels=["Fonte indisponível", "Mangá"],
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([ds_perm, ds_temp], 509, _cfg())
    assert dec.classification == "possible_duplicate"
    assert dec.should_auto_close is False


def test_d07_enhancement_requires_rich_non_extension_tokens_and_topics():
    e1 = make_raw(
        510,
        "[Sugestão]: Suki — Melhorar busca",
        "### Título ou resumo\nMelhorar carregamento\n### Descrição da melhoria\nMelhorar carregamento na Suki.\n### Extensão relacionada (opcional)\nSuki\n",
        labels=["Sugestão"],
        created_at="2026-11-01T10:00:00Z",
    )
    e2 = make_raw(
        511,
        "[Sugestão]: Suki — Melhorar carregamento",
        "### Título ou resumo\nMelhorar carregamento\n### Descrição da melhoria\nMelhorar carregamento na Suki.\n### Extensão relacionada (opcional)\nSuki\n",
        labels=["Sugestão"],
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([e1, e2], 511, _cfg())
    assert dec.should_auto_close is False


def test_d08_workflows_upgraded_to_node24_compatible_releases():
    wf_files = {
        "duplicate-guard.yml": {"actions/checkout@v7", "actions/setup-python@v7", "actions/cache@v6"},
        "ci-tests.yml": {"actions/checkout@v7", "actions/setup-python@v7"},
        "label-issues.yml": {"actions/github-script@v9"},
        "close-issue.yml": {"actions/github-script@v9"},
        "issue-closed-guard.yml": {"actions/github-script@v9"},
    }
    legacy_refs = ("actions/checkout@v4", "actions/setup-python@v5", "actions/cache@v4", "actions/github-script@v8")
    for fname, expected_refs in wf_files.items():
        text = (WORKFLOWS_DIR / fname).read_text(encoding="utf-8")
        yaml.safe_load(text)
        for leg in legacy_refs:
            assert leg not in text, f"{fname} still references legacy {leg}"
        for req in expected_refs:
            assert req in text, f"{fname} missing {req}"


def test_d09_real_snapshot_open_issues_zero_false_positives():
    real = load_real_fixtures()
    parsed = parse_corpus(list(real.values()))
    cat = build_extension_catalog(_hh_payload("en"))
    matcher = DuplicateMatcher(parsed, _cfg(), catalog=cat)
    open_issues = [p for p in parsed if p.is_open]
    assert len(open_issues) >= 10
    for p in open_issues:
        dec = matcher.evaluate(p)
        if p.raw.number == 149:
            assert dec.canonical_issue == 140
            assert dec.should_auto_close is True
        else:
            assert dec.should_auto_close is False, (
                f"Unexpected auto-close on open real issue #{p.raw.number}: "
                f"{dec.classification} -> #{dec.canonical_issue} ({dec.reasons})"
            )


def test_d10_concurrent_target_edited_at_pre_close_barrier_rolls_back():
    orig = _bug(512, "No pages found Naruto", "Erro No pages found no capítulo 12 de Naruto ao abrir leitor.")
    cand = _bug(
        513,
        "No pages found Naruto",
        "Erro No pages found no capítulo 12 de Naruto ao abrir leitor.",
        created_at="2026-11-02T10:00:00Z",
    )
    parsed = parse_corpus([orig, cand])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])
    assert dec.should_auto_close is True

    class ConcurrentEditAtBarrierApi(InMemoryGitHubApi):
        def __init__(self, issues):
            super().__init__(issues)
            self.target_gets = 0

        def get_issue(self, issue_number: int) -> dict:
            if issue_number == 513:
                self.target_gets += 1
                # On barrier check right before close (after label applied)
                if self.target_gets >= 3:
                    self.issues[513]["body"] = self.issues[513]["body"].replace("capítulo 12", "capítulo 99")
            return super().get_issue(issue_number)

    api = ConcurrentEditAtBarrierApi([orig, cand])
    res = GuardActionExecutor(api, _cfg(), {512: orig, 513: cand}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert api.issues[513]["state"] == "open"
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[513]["labels"]}


def test_d11_concurrent_author_clarification_at_barrier_rolls_back():
    orig = _bug(514, "No pages found Naruto", "Erro No pages found no capítulo 12 de Naruto ao abrir leitor.")
    cand = _bug(
        515,
        "No pages found Naruto",
        "Erro No pages found no capítulo 12 de Naruto ao abrir leitor.",
        created_at="2026-11-02T10:00:00Z",
    )
    parsed = parse_corpus([orig, cand])
    matcher = DuplicateMatcher(parsed, _cfg())
    dec = matcher.evaluate(parsed[1])

    class ConcurrentCommentAtBarrierApi(InMemoryGitHubApi):
        def __init__(self, issues):
            super().__init__(issues)
            self.target_comment_reads = 0

        def list_comments(self, issue_number: int) -> list[dict]:
            if issue_number == 515:
                self.target_comment_reads += 1
                if self.target_comment_reads >= 2:
                    return super().list_comments(issue_number) + [
                        {
                            "id": 9901,
                            "user": {"login": cand.author, "type": "User"},
                            "author_association": "NONE",
                            "body": "Na verdade ocorre no capítulo 22 de Bleach, não é duplicata.",
                        }
                    ]
            return super().list_comments(issue_number)

    api = ConcurrentCommentAtBarrierApi([orig, cand])
    res = GuardActionExecutor(api, _cfg(), {514: orig, 515: cand}, matcher=matcher).apply(dec)
    assert res.closed is False
    assert api.issues[515]["state"] == "open"
    assert LABEL_DUPLICATE not in {l["name"] for l in api.issues[515]["labels"]}
