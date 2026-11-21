# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Extreme adversarial, semantic, counterfactual, security, and operational test matrix
covering >= 120 distinct scenarios with zero auto-close false positives.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.duplicate_guard.actions import GuardActionExecutor, parse_marker  # noqa: E402
from scripts.duplicate_guard.cli import parse_corpus, run_cli  # noqa: E402
from scripts.duplicate_guard.config import load_config  # noqa: E402
from scripts.duplicate_guard.github_client import GitHubApiError, PermissionDeniedError  # noqa: E402
from scripts.duplicate_guard.matcher import DuplicateMatcher  # noqa: E402
from scripts.duplicate_guard.models import LABEL_DONE, LABEL_DUPLICATE, RULE_ENGINE_VERSION, GuardConfig, RawIssue  # noqa: E402
from scripts.duplicate_guard.parser import build_extension_catalog, parse_issue  # noqa: E402
from tests.test_duplicate_guard import InMemoryGitHubApi, make_raw  # noqa: E402

FIXTURES_PATH = REPO_ROOT / "tests" / "fixtures" / "real_issues_snapshot.json"


def _cfg() -> GuardConfig:
    return load_config(REPO_ROOT / ".github" / "duplicate-guard.json")


def _load_real() -> dict[int, RawIssue]:
    data = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    return {int(item["number"]): RawIssue.from_github_dict(item) for item in data["issues"]}


def _eval(
    issues: list[RawIssue],
    target_num: int,
    *,
    catalog=None,
    dup_links: dict[int, int] | None = None,
):
    parsed = parse_corpus(issues)
    matcher = DuplicateMatcher(parsed, _cfg(), duplicate_links=dup_links, catalog=catalog)
    target = next(p for p in parsed if p.raw.number == target_num)
    return matcher.evaluate(target)


def _cat(manga: list[dict] | None = None, anime: list[dict] | None = None):
    return build_extension_catalog({"manga": manga or [], "anime": anime or []})


def _make_ne(
    num: int,
    name: str,
    url: str,
    *,
    ext_type: str = "Mangá",
    lang: str = "Português (Brasil)",
    notes: str = "_No response_",
    state: str = "open",
    state_reason: str | None = None,
    labels: list[str] | None = None,
    created_at: str = "2026-11-01T10:00:00Z",
    closed_at: str | None = None,
) -> RawIssue:
    lbls = labels if labels is not None else ["Nova extensão", ext_type, "Em análise"]
    body = (
        f"### Tipo de extensão\n{ext_type}\n"
        f"### Nome da fonte\n{name}\n"
        f"### URL do site\n{url}\n"
        f"### Idioma\n{lang}\n"
        "### Precisa de login?\nNão\n"
        "### Precisa de VIP/Premium?\nNão\n"
        "### Conteúdo +18?\nNão\n"
        f"### Observações (opcional)\n{notes}\n"
    )
    return make_raw(
        num,
        f"[Nova extensão]: {name}",
        body,
        labels=lbls,
        state=state,
        state_reason=state_reason,
        created_at=created_at,
        closed_at=closed_at,
    )


def _make_bug(
    num: int,
    ext: str,
    desc: str,
    *,
    title_suffix: str = "",
    ext_type: str = "Mangá",
    part: str = "Leitor / Imagens",
    version: str = "1.4.2",
    repro: str = "",
    notes: str = "",
    state: str = "open",
    state_reason: str | None = None,
    labels: list[str] | None = None,
    created_at: str = "2026-11-01T10:00:00Z",
    closed_at: str | None = None,
    author: str = "user1",
) -> RawIssue:
    lbls = labels if labels is not None else ["Problema na extensão", ext_type, "Em análise"]
    title = f"[Problema na extensão]: {ext} — {title_suffix}" if title_suffix else f"[Problema na extensão]: {ext}"
    body = (
        f"### Tipo de extensão\n{ext_type}\n"
        f"### Nome da extensão\n{ext}\n"
        f"### Versão da extensão\n{version}\n"
        "### Aplicativo utilizado\nMihon\n"
        f"### Parte afetada\n{part}\n"
        f"### O que está acontecendo?\n{desc}\n"
        + (f"### Como reproduzir?\n{repro}\n" if repro else "")
        + (f"### Informações complementares (opcional)\n{notes}\n" if notes else "")
    )
    return make_raw(
        num,
        title,
        body,
        labels=lbls,
        state=state,
        state_reason=state_reason,
        created_at=created_at,
        closed_at=closed_at,
        author=author,
    )


def _make_dc(
    num: int,
    ext: str,
    old_url: str,
    new_url: str,
    *,
    ext_type: str = "Mangá",
    state: str = "open",
    state_reason: str | None = None,
    labels: list[str] | None = None,
    created_at: str = "2026-11-01T10:00:00Z",
    closed_at: str | None = None,
) -> RawIssue:
    lbls = labels if labels is not None else ["Mudança de domínio", ext_type, "Em análise"]
    body = (
        f"### Tipo de extensão\n{ext_type}\n"
        f"### Nome da extensão\n{ext}\n"
        f"### URL antiga\n{old_url}\n"
        f"### URL nova\n{new_url}\n"
    )
    return make_raw(
        num,
        f"[Mudança de domínio]: {ext}",
        body,
        labels=lbls,
        state=state,
        state_reason=state_reason,
        created_at=created_at,
        closed_at=closed_at,
    )


def _make_ds(
    num: int,
    ext: str,
    desc: str,
    *,
    notes: str = "",
    ext_type: str = "Mangá",
    state: str = "open",
    state_reason: str | None = None,
    labels: list[str] | None = None,
    created_at: str = "2026-11-01T10:00:00Z",
    closed_at: str | None = None,
) -> RawIssue:
    lbls = labels if labels is not None else ["Fonte indisponível", ext_type, "Em análise"]
    body = (
        f"### Tipo de extensão\n{ext_type}\n"
        f"### Nome da extensão\n{ext}\n"
        f"### O que aconteceu?\n{desc}\n"
        + (f"### Informações complementares (opcional)\n{notes}\n" if notes else "")
    )
    return make_raw(
        num,
        f"[Fonte indisponível]: {ext}",
        body,
        labels=lbls,
        state=state,
        state_reason=state_reason,
        created_at=created_at,
        closed_at=closed_at,
    )


def _make_enh(
    num: int,
    summary: str,
    desc: str,
    *,
    related_ext: str = "",
    extra: str = "",
    state: str = "open",
    state_reason: str | None = None,
    labels: list[str] | None = None,
    created_at: str = "2026-11-01T10:00:00Z",
    closed_at: str | None = None,
) -> RawIssue:
    lbls = labels if labels is not None else ["Sugestão", "Em análise"]
    body = (
        f"### Título ou resumo\n{summary}\n"
        f"### Descrição da melhoria\n{desc}\n"
        f"### Extensão relacionada (opcional)\n{related_ext}\n"
        + (f"### Informações complementares (opcional)\n{extra}\n" if extra else "")
    )
    return make_raw(
        num,
        f"[Sugestão]: {summary}",
        body,
        labels=lbls,
        state=state,
        state_reason=state_reason,
        created_at=created_at,
        closed_at=closed_at,
    )


# ===========================================================================
# 11.1 NOVA EXTENSÃO (16 cenários)
# ===========================================================================

@pytest.mark.parametrize("case_id,issues,target_num,use_cat,exp_cls,exp_close", [
    (
        "ne_01_exact_duplicate",
        [
            _make_ne(101, "KuroScans", "https://kuroscans.com"),
            _make_ne(102, "KuroScans", "https://kuroscans.com", created_at="2026-11-02T10:00:00Z"),
        ],
        102, False, "confirmed_duplicate", True,
    ),
    (
        "ne_02_divergent_mobile_and_subpath_requires_review",
        [
            _make_ne(101, "KuroScans", "http://kuroscans.com"),
            _make_ne(102, "KuroScans", "https://m.kuroscans.com/lista/?utm=1#top", created_at="2026-11-02T10:00:00Z"),
        ],
        102, False, "possible_duplicate", False,
    ),
    (
        "ne_03_divergent_www2_subdomain_requires_review",
        [
            _make_ne(101, "KuroScans", "https://www2.kuroscans.com"),
            _make_ne(102, "KuroScans", "https://kuroscans.com/home", created_at="2026-11-02T10:00:00Z"),
        ],
        102, False, "possible_duplicate", False,
    ),
    (
        "ne_04_distinct_shared_host_subdomains",
        [
            _make_ne(101, "AlphaTeam", "https://alphateam.pages.dev"),
            _make_ne(102, "BetaTeam", "https://betateam.pages.dev", created_at="2026-11-02T10:00:00Z"),
        ],
        102, False, "no_match", False,
    ),
    (
        "ne_05_typo_name_same_domain",
        [
            _make_ne(101, "ShadowScans", "https://shadowscans.org"),
            _make_ne(102, "Shaodw Scans", "https://shadowscans.org/", created_at="2026-11-02T10:00:00Z"),
        ],
        102, False, "confirmed_duplicate", True,
    ),
    (
        "ne_06_same_name_different_domain_never_closes",
        [
            _make_ne(101, "MoonRead", "https://moonread.com"),
            _make_ne(102, "MoonRead", "https://moonread.net", created_at="2026-11-02T10:00:00Z"),
        ],
        102, False, "possible_duplicate", False,
    ),
    (
        "ne_07_same_site_different_language_pt_vs_es",
        [
            _make_ne(101, "MultilangSite", "https://multilangsite.com/pt/", lang="Português (Brasil)"),
            _make_ne(102, "MultilangSite", "https://multilangsite.com/es/", lang="Espanhol", created_at="2026-11-02T10:00:00Z"),
        ],
        102, False, "no_match", False,
    ),
    (
        "ne_08_catalog_verified_same_language_auto_closes",
        [
            _make_ne(102, "CatSite", "https://catsite.org", lang="Português (Brasil)"),
        ],
        102, True, "confirmed_duplicate", True,
    ),
    (
        "ne_09_catalog_verified_only_other_language_en_vs_pt",
        [
            _make_ne(102, "OnlyEnSite", "https://onlyensite.com", lang="Português (Brasil)"),
        ],
        102, True, "no_match", False,
    ),
    (
        "ne_10_catalog_incomplete_empty_languages_never_closes",
        [
            _make_ne(102, "NoLangMeta", "https://nolangmeta.com", lang="Português (Brasil)"),
        ],
        102, True, "no_match", False,
    ),
    (
        "ne_11_catalog_unknown_or_exotic_language_never_closes",
        [
            _make_ne(102, "CatSite", "https://catsite.org", lang="Tagalog"),
        ],
        102, True, "no_match", False,
    ),
    (
        "ne_12_catalog_all_requested_vs_en_es_only_never_closes",
        [
            _make_ne(102, "BilingualSite", "https://bilingual.org", lang="Todos os idiomas"),
        ],
        102, True, "no_match", False,
    ),
    (
        "ne_13_catalog_all_requested_vs_true_all_auto_closes",
        [
            _make_ne(102, "UniversalSite", "https://universal.org", lang="Todos os idiomas"),
        ],
        102, True, "confirmed_duplicate", True,
    ),
    (
        "ne_14_creation_that_is_language_enhancement_preserved",
        [
            _make_ne(101, "OnlyEnSite", "https://onlyensite.com", lang="Português (Brasil)", state="closed", state_reason="completed", labels=["Nova extensão", "Mangá", "Concluído"]),
            _make_ne(
                102, "OnlyEnSite", "https://onlyensite.com", lang="Português (Brasil)",
                notes="A extensão já existe no catálogo mas apenas em inglês; preciso adicionar suporte ao conteúdo em português.",
                created_at="2026-11-05T10:00:00Z",
            ),
        ],
        102, True, "no_match", False,
    ),
    (
        "ne_15_creation_that_is_bug_report_preserved",
        [
            _make_ne(101, "CatSite", "https://catsite.org", lang="Português (Brasil)"),
            _make_ne(
                102, "CatSite", "https://catsite.org", lang="Português (Brasil)",
                notes="A extensão já existe no repositório porém parou de funcionar com erro HTTP 500 ao carregar capítulos.",
                created_at="2026-11-05T10:00:00Z",
            ),
        ],
        102, True, "no_match", False,
    ),
    (
        "ne_16_manga_vs_anime_same_site_never_closes",
        [
            _make_ne(101, "DualMedia", "https://dualmedia.tv", ext_type="Mangá"),
            _make_ne(102, "DualMedia", "https://dualmedia.tv", ext_type="Anime", created_at="2026-11-02T10:00:00Z"),
        ],
        102, False, "no_match", False,
    ),
])
def test_11_1_new_extension_adversarial_matrix(case_id, issues, target_num, use_cat, exp_cls, exp_close):
    catalog = _cat(
        manga=[
            {"name": "CatSite", "pkg": "p.catsite", "lang": "pt-BR", "version": "1.0", "sources": [{"name": "CatSite", "lang": "pt-BR", "baseUrl": "https://catsite.org"}]},
            {"name": "OnlyEnSite", "pkg": "p.onlyen", "lang": "en", "version": "1.0", "sources": [{"name": "OnlyEnSite", "lang": "en", "baseUrl": "https://onlyensite.com"}]},
            {"name": "NoLangMeta", "pkg": "p.nolang", "lang": "", "version": "1.0", "sources": [{"name": "NoLangMeta", "lang": "", "baseUrl": "https://nolangmeta.com"}]},
            {"name": "BilingualSite", "pkg": "p.bi", "lang": "en", "version": "1.0", "sources": [
                {"name": "BilingualSite EN", "lang": "en", "baseUrl": "https://bilingual.org"},
                {"name": "BilingualSite ES", "lang": "es", "baseUrl": "https://bilingual.org"},
            ]},
            {"name": "UniversalSite", "pkg": "p.uni", "lang": "all", "version": "1.0", "sources": [{"name": "UniversalSite", "lang": "all", "baseUrl": "https://universal.org"}]},
        ]
    ) if use_cat else None
    dec = _eval(issues, target_num, catalog=catalog)
    assert dec.classification == exp_cls, f"{case_id}: got {dec.classification} ({dec.reasons})"
    assert dec.should_auto_close is exp_close, f"{case_id}: should_auto_close={dec.should_auto_close}"
    if exp_close and dec.match_kind == "already_fulfilled_creation" and dec.canonical_issue is None:
        assert dec.resolution_label == LABEL_DONE
        assert dec.close_state_reason == "completed"


def test_11_1_simultaneous_open_requests_same_second_winner():
    same_ts = "2026-11-18T12:00:00Z"
    i1 = _make_ne(10, "SimultSite", "https://simultsite.com", created_at=same_ts)
    i2 = _make_ne(11, "SimultSite", "https://simultsite.com", created_at=same_ts)
    i3 = _make_ne(12, "SimultSite", "https://simultsite.com", created_at=same_ts)
    d10 = _eval([i1, i2, i3], 10)
    d11 = _eval([i1, i2, i3], 11)
    d12 = _eval([i1, i2, i3], 12)
    assert d10.should_auto_close is False
    assert d11.should_auto_close is True and d11.canonical_issue == 10
    assert d12.should_auto_close is True and d12.canonical_issue == 10


# ===========================================================================
# 11.2 PROBLEMA NA EXTENSÃO (25 cenários)
# ===========================================================================

@pytest.mark.parametrize("case_id,issues,target_num,allowed_cls,exp_close", [
    (
        "bug_01_sushi_403_reader_vs_login_free_text_part_outro",
        [
            _make_bug(300, "Sushi Manga", "Extensão Sushi Manga apresenta HTTP 403 quando abre o leitor.", part="Outro", version="", created_at="2026-11-10T10:00:00Z"),
            _make_bug(301, "Sushi Manga", "Extensão Sushi Manga apresenta HTTP 403 quando tenta fazer login.", part="Outro", version="", created_at="2026-11-11T10:00:00Z"),
        ],
        301, ("no_match",), False,
    ),
    (
        "bug_02_sushi_generic_http400_never_confirmed",
        [
            _make_bug(302, "Sushi Manga", "Sushi Manga não está funcionando. Aparece HTTP 400.", part="Outro", version="", created_at="2026-11-10T10:00:00Z"),
            _make_bug(303, "Sushi Manga", "Sushi Manga não funciona. Erro HTTP 400.", part="Outro", version="", created_at="2026-11-11T10:00:00Z"),
        ],
        303, ("possible_duplicate",), False,
    ),
    (
        "bug_03_sushi_specific_no_pages_ch12_v142_confirmed",
        [
            _make_bug(304, "Sushi Manga", "Erro No pages found ao abrir o capítulo 12 da obra ABC no leitor.", title_suffix="No pages found cap 12", part="Leitor / Imagens", version="1.4.2", created_at="2026-11-10T10:00:00Z"),
            _make_bug(305, "Sushi Manga", "Ao tentar ler o capítulo 12 da obra ABC no leitor aparece No pages found.", title_suffix="Sem páginas cap 12 obra ABC", part="Leitor / Imagens", version="1.4.2", created_at="2026-11-11T10:00:00Z"),
        ],
        305, ("confirmed_duplicate",), True,
    ),
    (
        "bug_04_scenario_a_current_open_incident_confirmed",
        [
            _make_bug(400, "Sushi Manga", "JsonDecodingException unexpected JSON token at offset 0 <!DOCTYPE html> ao carregar lista de populares na versão 1.4.2.", title_suffix="Erro JSON", part="Popular / Recentes", version="1.4.2", created_at="2026-11-10T10:00:00Z"),
            _make_bug(401, "Sushi Manga", "Na versão 1.4.2 o catálogo populares retorna JsonDecodingException unexpected JSON token <!DOCTYPE html>.", title_suffix="JsonDecodingException", part="Popular / Recentes", version="1.4.2", created_at="2026-11-12T10:00:00Z"),
        ],
        401, ("confirmed_duplicate",), True,
    ),
    (
        "bug_05_scenario_b_regression_after_2yr_concluido",
        [
            _make_bug(400, "Sushi Manga", "No pages found ao tentar ler o capítulo 12 da obra ABC.", part="Leitor / Imagens", version="1.4.2", state="closed", state_reason="completed", labels=["Problema na extensão", "Mangá", "Concluído"], created_at="2024-06-01T10:00:00Z", closed_at="2024-06-05T10:00:00Z"),
            _make_bug(402, "Sushi Manga", "No pages found ao tentar ler o capítulo 12 da obra ABC.", part="Leitor / Imagens", version="1.4.2", created_at="2026-11-01T10:00:00Z"),
        ],
        402, ("no_match",), False,
    ),
    (
        "bug_06_scenario_c_stale_open_intervening_concluido_fix",
        [
            _make_bug(403, "Sushi Manga", "No pages found ao abrir capítulos no leitor.", part="Leitor / Imagens", version="1.2.0", created_at="2025-11-01T10:00:00Z"),
            _make_bug(404, "Sushi Manga", "Corrigido parser de páginas do leitor.", part="Leitor / Imagens", version="1.3.0", state="closed", state_reason="completed", labels=["Problema na extensão", "Mangá", "Concluído"], created_at="2026-01-10T10:00:00Z", closed_at="2026-01-15T10:00:00Z"),
            _make_bug(405, "Sushi Manga", "No pages found ao abrir capítulos no leitor.", part="Leitor / Imagens", version="1.4.2", created_at="2026-11-01T10:00:00Z"),
        ],
        405, ("no_match", "possible_duplicate"), False,
    ),
    (
        "bug_07_scenario_d_recurrent_cloudflare_distant_dates_without_fix",
        [
            _make_bug(406, "Sushi Manga", "Falha ao contornar Cloudflare Turnstile no endpoint api/chapters na v1.4.2.", part="Capítulos", version="1.4.2", created_at="2026-01-10T10:00:00Z"),
            _make_bug(407, "Sushi Manga", "Falha ao contornar Cloudflare Turnstile no endpoint api/chapters na v1.4.2.", part="Capítulos", version="1.4.2", created_at="2026-11-10T10:00:00Z"),
        ],
        407, ("possible_duplicate",), False,
    ),
    (
        "bug_08_identical_open_bug_2_years_apart_never_closes",
        [
            _make_bug(408, "Sushi Manga", "Erro No pages found no capítulo 12 da obra ABC.", part="Leitor / Imagens", version="", created_at="2024-11-01T10:00:00Z"),
            _make_bug(409, "Sushi Manga", "Erro No pages found no capítulo 12 da obra ABC.", part="Leitor / Imagens", version="", created_at="2026-11-01T10:00:00Z"),
        ],
        409, ("possible_duplicate",), False,
    ),
    (
        "bug_09_404_single_work_vs_all_works",
        [
            _make_bug(410, "Sushi Manga", "Erro HTTP 404 apenas na obra Naruto ao tentar abrir capítulos.", part="Capítulos"),
            _make_bug(411, "Sushi Manga", "Erro HTTP 404 em todas as obras ao tentar abrir capítulos.", part="Capítulos", created_at="2026-11-02T10:00:00Z"),
        ],
        411, ("no_match", "possible_duplicate"), False,
    ),
    (
        "bug_10_http_500_search_vs_catalog",
        [
            _make_bug(412, "Sushi Manga", "Erro HTTP 500 ao pesquisar títulos na busca.", part="Busca / Filtros"),
            _make_bug(413, "Sushi Manga", "Erro HTTP 500 ao carregar recentes no catálogo.", part="Popular / Recentes", created_at="2026-11-02T10:00:00Z"),
        ],
        413, ("no_match", "possible_duplicate"), False,
    ),
    (
        "bug_11_cloudflare_reader_vs_login",
        [
            _make_bug(414, "Sushi Manga", "Cloudflare bloqueando imagens no leitor.", part="Leitor / Imagens"),
            _make_bug(415, "Sushi Manga", "Cloudflare bloqueando na tela de login.", part="Login", created_at="2026-11-02T10:00:00Z"),
        ],
        415, ("no_match",), False,
    ),
    (
        "bug_12_specific_work_a_vs_work_b",
        [
            _make_bug(416, "Sushi Manga", "Só na obra Solo Leveling aparece No pages found.", part="Leitor / Imagens"),
            _make_bug(417, "Sushi Manga", "Só na obra One Piece aparece No pages found.", part="Leitor / Imagens", created_at="2026-11-02T10:00:00Z"),
        ],
        417, ("no_match", "possible_duplicate"), False,
    ),
    (
        "bug_13_failure_after_extension_version_bump_142_vs_145",
        [
            _make_bug(418, "Sushi Manga", "Erro No pages found no capítulo 12 da obra ABC.", part="Leitor / Imagens", version="1.4.2"),
            _make_bug(419, "Sushi Manga", "Erro No pages found no capítulo 12 da obra ABC.", part="Leitor / Imagens", version="1.4.5", created_at="2026-11-02T10:00:00Z"),
        ],
        419, ("possible_duplicate",), False,
    ),
    (
        "bug_14_explicit_regression_cue_after_update",
        [
            _make_bug(420, "Sushi Manga", "Erro No pages found ao abrir capítulos.", part="Leitor / Imagens", version="1.4.2"),
            _make_bug(421, "Sushi Manga", "Antes funcionava normalmente, mas voltou a ocorrer No pages found após atualização.", part="Leitor / Imagens", version="1.4.2", created_at="2026-11-02T10:00:00Z"),
        ],
        421, ("no_match", "possible_duplicate"), False,
    ),
    (
        "bug_15_short_vague_description_never_closes",
        [
            _make_bug(422, "Sushi Manga", "não funciona", part="Outro", version=""),
            _make_bug(423, "Sushi Manga", "parou de funcionar", part="Outro", version="", created_at="2026-11-02T10:00:00Z"),
        ],
        423, ("no_match",), False,
    ),
    (
        "bug_16_different_affected_parts_catalog_vs_install",
        [
            _make_bug(424, "Sushi Manga", "Não carrega populares", part="Popular / Recentes"),
            _make_bug(425, "Sushi Manga", "Não instala APK no Mihon", part="Instalação / Atualização", created_at="2026-11-02T10:00:00Z"),
        ],
        425, ("no_match",), False,
    ),
    (
        "bug_17_download_slow_vs_no_pages_same_chapters_part",
        [
            _make_bug(426, "Sushi Manga", "Download lento ao baixar capítulos offline.", part="Capítulos"),
            _make_bug(427, "Sushi Manga", "Nenhuma página encontrada ao abrir capítulos.", part="Capítulos", created_at="2026-11-02T10:00:00Z"),
        ],
        427, ("no_match", "possible_duplicate"), False,
    ),
    (
        "bug_18_contradictory_http_codes_403_vs_502",
        [
            _make_bug(428, "Sushi Manga", "Erro HTTP 403 Forbidden ao listar capítulos.", part="Capítulos"),
            _make_bug(429, "Sushi Manga", "Erro HTTP 502 Bad Gateway ao listar capítulos.", part="Capítulos", created_at="2026-11-02T10:00:00Z"),
        ],
        429, ("no_match",), False,
    ),
    (
        "bug_19_different_exceptions_npe_vs_json",
        [
            _make_bug(430, "Sushi Manga", "java.lang.NullPointerException: Attempt to invoke virtual method on a null object reference", part="Metadados"),
            _make_bug(431, "Sushi Manga", "JsonDecodingException: Unexpected JSON token at offset 0", part="Metadados", created_at="2026-11-02T10:00:00Z"),
        ],
        431, ("no_match",), False,
    ),
    (
        "bug_20_missing_chapters_vs_wrong_chapters",
        [
            _make_bug(432, "Sushi Manga", "Não estão aparecendo todos os capítulos, só mostra 5 capítulos.", part="Capítulos"),
            _make_bug(433, "Sushi Manga", "Capítulos errados e fora de ordem abrindo de outra obra.", part="Capítulos", created_at="2026-11-02T10:00:00Z"),
        ],
        433, ("no_match", "possible_duplicate"), False,
    ),
    (
        "bug_21_adjacent_parts_chapters_reader_with_specific_no_pages_confirmed",
        [
            _make_bug(434, "Sushi Manga", "Erro No pages found no capítulo 12 da obra XYZ.", part="Capítulos", version="1.4.2"),
            _make_bug(435, "Sushi Manga", "No pages found ao tentar ler o capítulo 12 da obra XYZ.", part="Leitor / Imagens", version="1.4.2", created_at="2026-11-02T10:00:00Z"),
        ],
        435, ("confirmed_duplicate",), True,
    ),
    (
        "bug_22_generic_cloudflare_wording_only_never_closes",
        [
            _make_bug(436, "Sushi Manga", "Cloudflare bloqueando o leitor.", part="Leitor / Imagens"),
            _make_bug(437, "Sushi Manga", "Falha ao contornar a Cloudflare no leitor.", part="Leitor / Imagens", created_at="2026-11-02T10:00:00Z"),
        ],
        437, ("possible_duplicate",), False,
    ),
    (
        "bug_23_generic_timeout_wording_never_closes",
        [
            _make_bug(438, "Sushi Manga", "SocketTimeoutException timed out na extensão.", part="Outro"),
            _make_bug(439, "Sushi Manga", "Erro timed out ao acessar a extensão.", part="Outro", created_at="2026-11-02T10:00:00Z"),
        ],
        439, ("possible_duplicate",), False,
    ),
    (
        "bug_24_anime_video_playback_vs_audio_subtitle_desync",
        [
            _make_bug(440, "Sushi Anime", "Não reproduz nenhum episódio no player.", ext_type="Anime", part="Episódios / Vídeo"),
            _make_bug(441, "Sushi Anime", "Áudio e legenda dessincronizados no episódio 3.", ext_type="Anime", part="Episódios / Vídeo", created_at="2026-11-02T10:00:00Z"),
        ],
        441, ("no_match", "possible_duplicate"), False,
    ),
    (
        "bug_25_identical_rich_descriptive_non_generic_endpoint_confirmed",
        [
            _make_bug(442, "Sushi Manga", "Ao abrir obra aparece aviso redirecionando rota /maintenance/blocked no endpoint graphql/chapters retornando HTML 403.", part="Capítulos", version="1.5.0"),
            _make_bug(443, "Sushi Manga", "Ao abrir qualquer obra o endpoint graphql/chapters redireciona rota /maintenance/blocked retornando HTML 403.", part="Capítulos", version="1.5.0", created_at="2026-11-02T10:00:00Z"),
        ],
        443, ("confirmed_duplicate",), True,
    ),
])
def test_11_2_bug_adversarial_matrix(case_id, issues, target_num, allowed_cls, exp_close):
    dec = _eval(issues, target_num)
    assert dec.classification in allowed_cls, f"{case_id}: got {dec.classification} ({dec.reasons})"
    assert dec.should_auto_close is exp_close, f"{case_id}: should_auto_close={dec.should_auto_close}"


# ===========================================================================
# 11.3 MUDANÇA DE DOMÍNIO (12 cenários)
# ===========================================================================

@pytest.mark.parametrize("case_id,issues,target_num,exp_cls,exp_close", [
    (
        "dc_01_same_migration_open",
        [
            _make_dc(501, "AlphaScan", "https://alphascan.org", "https://alphascan.net"),
            _make_dc(502, "AlphaScan", "https://alphascan.org", "https://alphascan.net", created_at="2026-11-02T10:00:00Z"),
        ],
        502, "confirmed_duplicate", True,
    ),
    (
        "dc_02_normalized_www_protocol",
        [
            _make_dc(501, "AlphaScan", "http://www.alphascan.org/", "https://alphascan.net"),
            _make_dc(502, "AlphaScan", "https://alphascan.org", "http://www.alphascan.net/", created_at="2026-11-02T10:00:00Z"),
        ],
        502, "confirmed_duplicate", True,
    ),
    (
        "dc_03_different_new_domain",
        [
            _make_dc(501, "AlphaScan", "https://alphascan.org", "https://alphascan.net"),
            _make_dc(502, "AlphaScan", "https://alphascan.org", "https://alphascan.tv", created_at="2026-11-02T10:00:00Z"),
        ],
        502, "no_match", False,
    ),
    (
        "dc_04_subsequent_migration_a_to_b_then_b_to_c",
        [
            _make_dc(501, "AlphaScan", "https://alphascan.org", "https://alphascan.net", state="closed", state_reason="completed", labels=["Mudança de domínio", "Mangá", "Concluído"], created_at="2026-06-01T10:00:00Z", closed_at="2026-06-02T10:00:00Z"),
            _make_dc(502, "AlphaScan", "https://alphascan.net", "https://alphascan.xyz", created_at="2026-11-01T10:00:00Z"),
        ],
        502, "no_match", False,
    ),
    (
        "dc_05_deceptive_tld_net_vs_tv",
        [
            _make_dc(501, "MangaLivre", "https://mangalivre.com", "https://mangalivre.net"),
            _make_dc(502, "MangaLivre", "https://mangalivre.com", "https://mangalivre.tv", created_at="2026-11-02T10:00:00Z"),
        ],
        502, "no_match", False,
    ),
    (
        "dc_06_deceptive_typosquat_live_vs_livre",
        [
            _make_dc(501, "MangaLivre", "https://mangalivre.com", "https://mangalivre.to"),
            _make_dc(502, "MangaLivre", "https://mangalivre.com", "https://mangalive.to", created_at="2026-11-02T10:00:00Z"),
        ],
        502, "no_match", False,
    ),
    (
        "dc_07_invalid_incomplete_new_url",
        [
            _make_dc(501, "AlphaScan", "https://alphascan.org", "alphascan-novo"),
            _make_dc(502, "AlphaScan", "https://alphascan.org", "alphascan-novo", created_at="2026-11-02T10:00:00Z"),
        ],
        502, "possible_duplicate", False,
    ),
    (
        "dc_08_old_url_mismatch_same_new",
        [
            _make_dc(501, "AlphaScan", "https://alphascan.org", "https://alphascan.net"),
            _make_dc(502, "AlphaScan", "https://alphascan.com", "https://alphascan.net", created_at="2026-11-02T10:00:00Z"),
        ],
        502, "possible_duplicate", False,
    ),
    (
        "dc_09_shared_host_subdomains_distinct",
        [
            _make_dc(501, "AlphaScan", "https://old.pages.dev", "https://new1.pages.dev"),
            _make_dc(502, "AlphaScan", "https://old.pages.dev", "https://new2.pages.dev", created_at="2026-11-02T10:00:00Z"),
        ],
        502, "no_match", False,
    ),
    (
        "dc_10_same_migration_already_closed",
        [
            _make_dc(501, "AlphaScan", "https://alphascan.org", "https://alphascan.net", state="closed", state_reason="completed", labels=["Mudança de domínio", "Mangá", "Concluído"]),
            _make_dc(502, "AlphaScan", "https://alphascan.org", "https://alphascan.net", created_at="2026-11-02T10:00:00Z"),
        ],
        502, "possible_duplicate", False,
    ),
    (
        "dc_11_manga_vs_anime_same_brand",
        [
            _make_dc(501, "NoxPortal", "https://noxportal.com", "https://noxportal.org", ext_type="Mangá"),
            _make_dc(502, "NoxPortal", "https://noxportal.com", "https://noxportal.org", ext_type="Anime", created_at="2026-11-02T10:00:00Z"),
        ],
        502, "no_match", False,
    ),
    (
        "dc_12_missing_old_url_possible_only",
        [
            _make_dc(501, "AlphaScan", "", "https://alphascan.net"),
            _make_dc(502, "AlphaScan", "https://alphascan.org", "https://alphascan.net", created_at="2026-11-02T10:00:00Z"),
        ],
        502, "possible_duplicate", False,
    ),
])
def test_11_3_domain_change_adversarial_matrix(case_id, issues, target_num, exp_cls, exp_close):
    dec = _eval(issues, target_num)
    assert dec.classification == exp_cls, f"{case_id}: got {dec.classification}"
    assert dec.should_auto_close is exp_close


# ===========================================================================
# 11.4 FONTE INDISPONÍVEL / MORTA (12 cenários)
# ===========================================================================

@pytest.mark.parametrize("case_id,issues,target_num,allowed_cls,exp_close", [
    (
        "ds_01_both_permanent_with_evidence_same_window",
        [
            _make_ds(601, "YamiScan", "O domínio expirou há semanas e equipe confirmou encerramento definitivo no Discord.", notes="https://yamiscan.com NXDOMAIN"),
            _make_ds(602, "YamiScan", "Domínio expirado há semanas com anúncio oficial de encerramento definitivo.", notes="https://yamiscan.com NXDOMAIN", created_at="2026-11-03T10:00:00Z"),
        ],
        602, ("confirmed_duplicate",), True,
    ),
    (
        "ds_02_temporary_maintenance_vs_permanent",
        [
            _make_ds(601, "YamiScan", "Site em manutenção temporária hoje com erro 503."),
            _make_ds(602, "YamiScan", "Domínio expirou e confirmaram encerramento definitivo.", created_at="2026-11-02T10:00:00Z"),
        ],
        602, ("possible_duplicate",), False,
    ),
    (
        "ds_03_cloudflare_regional_block_vs_dead",
        [
            _make_ds(601, "YamiScan", "Cloudflare 522 caiu hoje apenas na operadora local."),
            _make_ds(602, "YamiScan", "Domínio expirou e encerrou definitivamente.", created_at="2026-11-02T10:00:00Z"),
        ],
        602, ("possible_duplicate",), False,
    ),
    (
        "ds_04_site_returned_and_died_months_later",
        [
            _make_ds(601, "YamiScan", "Domínio expirou e equipe confirmou encerramento definitivo.", state="closed", state_reason="completed", labels=["Fonte indisponível", "Mangá", "Concluído"], created_at="2026-06-01T10:00:00Z", closed_at="2026-06-05T10:00:00Z"),
            _make_ds(602, "YamiScan", "Domínio expirou novamente e equipe confirmou encerramento definitivo.", created_at="2026-11-01T10:00:00Z"),
        ],
        602, ("no_match",), False,
    ),
    (
        "ds_05_different_sources_both_dead",
        [
            _make_ds(601, "ScanAlpha", "Encerramento definitivo confirmado."),
            _make_ds(602, "ScanBeta", "Encerramento definitivo confirmado.", created_at="2026-11-02T10:00:00Z"),
        ],
        602, ("no_match",), False,
    ),
    (
        "ds_06_conflicting_urls_same_name",
        [
            _make_ds(601, "YamiScan", "Encerramento definitivo confirmado.", notes="https://yamiscan.com"),
            _make_ds(602, "YamiScan", "Encerramento definitivo confirmado.", notes="https://yamiscan.org", created_at="2026-11-02T10:00:00Z"),
        ],
        602, ("no_match",), False,
    ),
    (
        "ds_07_outside_30_day_window",
        [
            _make_ds(601, "YamiScan", "Encerramento definitivo confirmado."),
            _make_ds(602, "YamiScan", "Encerramento definitivo confirmado.", created_at="2026-12-20T10:00:00Z"),
        ],
        602, ("no_match",), False,
    ),
    (
        "ds_08_unclear_vs_permanent",
        [
            _make_ds(601, "YamiScan", "Site não abre desde ontem."),
            _make_ds(602, "YamiScan", "Domínio expirou há semanas e equipe confirmou encerramento definitivo.", created_at="2026-11-02T10:00:00Z"),
        ],
        602, ("no_match", "possible_duplicate"), False,
    ),
    (
        "ds_09_both_temporary_outage_never_closes",
        [
            _make_ds(601, "YamiScan", "Erro 503 em manutenção temporária hoje."),
            _make_ds(602, "YamiScan", "Instabilidade temporária agora com erro 502.", created_at="2026-11-02T10:00:00Z"),
        ],
        602, ("no_match",), False,
    ),
    (
        "ds_10_misfiled_domain_change_inside_dead_source",
        [
            _make_ds(601, "YamiScan", "Domínio expirou e equipe confirmou encerramento definitivo."),
            _make_ds(602, "YamiScan", "Não fechou, só mudou de domínio para novo domínio https://yamiscan.tv", created_at="2026-11-02T10:00:00Z"),
        ],
        602, ("no_match",), False,
    ),
    (
        "ds_11_manga_vs_anime",
        [
            _make_ds(601, "Yami", "Encerramento definitivo confirmado.", ext_type="Mangá"),
            _make_ds(602, "Yami", "Encerramento definitivo confirmado.", ext_type="Anime", created_at="2026-11-02T10:00:00Z"),
        ],
        602, ("no_match",), False,
    ),
    (
        "ds_12_closed_permanent_never_auto_closes_new",
        [
            _make_ds(601, "YamiScan", "Domínio expirou há semanas e equipe confirmou encerramento definitivo.", state="closed", state_reason="completed", labels=["Fonte indisponível", "Mangá", "Concluído"]),
            _make_ds(602, "YamiScan", "Domínio expirou há semanas e equipe confirmou encerramento definitivo.", created_at="2026-11-02T10:00:00Z"),
        ],
        602, ("possible_duplicate",), False,
    ),
])
def test_11_4_dead_source_adversarial_matrix(case_id, issues, target_num, allowed_cls, exp_close):
    dec = _eval(issues, target_num)
    assert dec.classification in allowed_cls, f"{case_id}: got {dec.classification}"
    assert dec.should_auto_close is exp_close


# ===========================================================================
# 11.5 MELHORIAS E FUNCIONALIDADES (14 cenários)
# ===========================================================================

@pytest.mark.parametrize("case_id,issues,target_num,allowed_cls,exp_close", [
    (
        "en_01_identical_extension_feature",
        [
            _make_enh(701, "Filtro por gênero na busca", "Adicionar filtro por gênero e ano na busca da extensão.", related_ext="SukiMangas"),
            _make_enh(702, "Filtro por gênero na busca", "Adicionar filtro por gênero e ano na busca da extensão.", related_ext="SukiMangas", created_at="2026-11-02T10:00:00Z"),
        ],
        702, ("confirmed_duplicate",), True,
    ),
    (
        "en_02_identical_global_feature",
        [
            _make_enh(701, "Exportar histórico em JSON", "Permitir exportar histórico em JSON para backup."),
            _make_enh(702, "Exportar histórico em JSON", "Permitir exportar histórico em JSON para backup.", created_at="2026-11-02T10:00:00Z"),
        ],
        702, ("confirmed_duplicate",), True,
    ),
    (
        "en_03_same_ext_filters_vs_login",
        [
            _make_enh(701, "Filtro por gênero", "Adicionar filtro por gênero na busca avançada.", related_ext="SukiMangas"),
            _make_enh(702, "Login de usuário", "Adicionar suporte a fazer login com conta na extensão.", related_ext="SukiMangas", created_at="2026-11-02T10:00:00Z"),
        ],
        702, ("no_match",), False,
    ),
    (
        "en_04_same_ext_batch_download_vs_reader_layout",
        [
            _make_enh(701, "Download em lote", "Permitir download em lote e exportar para CBZ.", related_ext="SukiMangas"),
            _make_enh(702, "Modo leitura página dupla", "Adicionar modo leitura webtoon e página dupla.", related_ext="SukiMangas", created_at="2026-11-02T10:00:00Z"),
        ],
        702, ("no_match",), False,
    ),
    (
        "en_05_lang_support_pt_vs_en",
        [
            _make_enh(701, "Suporte a português", "Adicionar suporte ao idioma português.", related_ext="HentaiHaven"),
            _make_enh(702, "Suporte a espanhol", "Adicionar suporte ao idioma espanhol.", related_ext="HentaiHaven", created_at="2026-11-02T10:00:00Z"),
        ],
        702, ("no_match",), False,
    ),
    (
        "en_06_video_quality_vs_cloudflare_bypass",
        [
            _make_enh(701, "Qualidade 1080p no player", "Escolher servidor alternativo e qualidade 1080p.", related_ext="AnimeFire"),
            _make_enh(702, "Bypass Cloudflare com DoH", "Contornar Cloudflare com custom DNS DoH.", related_ext="AnimeFire", created_at="2026-11-02T10:00:00Z"),
        ],
        702, ("no_match",), False,
    ),
    (
        "en_07_global_vs_extension_specific_never_mixed",
        [
            _make_enh(701, "Filtro por gênero", "Adicionar filtro por gênero no catálogo."),
            _make_enh(702, "Filtro por gênero na Bakai", "Adicionar filtro por gênero na extensão Bakai.", related_ext="Bakai", created_at="2026-11-02T10:00:00Z"),
        ],
        702, ("no_match",), False,
    ),
    (
        "en_08_previously_completed_feature_adjustment",
        [
            _make_enh(701, "Filtro por gênero na busca", "Adicionar filtro por gênero e ano na busca da extensão.", related_ext="SukiMangas", state="closed", state_reason="completed", labels=["Sugestão", "Concluído"]),
            _make_enh(702, "Ajuste no filtro por gênero na busca", "Melhorar filtro por gênero e ano na busca da extensão incluindo tags.", related_ext="SukiMangas", created_at="2026-11-10T10:00:00Z"),
        ],
        702, ("no_match", "possible_duplicate"), False,
    ),
    (
        "en_09_distinct_extensions_same_feature",
        [
            _make_enh(701, "Login na Blackout Comics", "Adicionar login na Blackout Comics.", related_ext="Blackout Comics"),
            _make_enh(702, "Login na Hanami Heaven", "Adicionar login na Hanami Heaven.", related_ext="Hanami Heaven", created_at="2026-11-02T10:00:00Z"),
        ],
        702, ("no_match",), False,
    ),
    (
        "en_10_moderate_paraphrase_human_review",
        [
            _make_enh(701, "Filtros de busca por gênero e tags", "Adicionar filtros para gênero, tags e ano na pesquisa avançada da fonte.", related_ext="NoxScan"),
            _make_enh(702, "Melhoria nos filtros de busca com categorias", "Incluir seleção de múltiplos gêneros e classificação por ano na barra de busca.", related_ext="NoxScan", created_at="2026-11-02T10:00:00Z"),
        ],
        702, ("possible_duplicate",), False,
    ),
    (
        "en_11_anilist_tracker_vs_cbz_export",
        [
            _make_enh(701, "Sincronizar com AniList", "Sincronizar progresso automaticamente com AniList."),
            _make_enh(702, "Exportar para CBZ e PDF", "Exportar para CBZ e PDF no download em lote.", created_at="2026-11-02T10:00:00Z"),
        ],
        702, ("no_match",), False,
    ),
    (
        "en_12_nsfw_toggle_vs_catalog_sections",
        [
            _make_enh(701, "Ocultar NSFW", "Adicionar modo seguro para ocultar NSFW.", related_ext="NoxScan"),
            _make_enh(702, "Aba de populares e recentes", "Organizar seção populares e recentes no catálogo.", related_ext="NoxScan", created_at="2026-11-02T10:00:00Z"),
        ],
        702, ("no_match",), False,
    ),
    (
        "en_13_language_en_vs_ja",
        [
            _make_enh(701, "Conteúdo em inglês", "Adicionar conteúdo em inglês na fonte.", related_ext="MultiSite"),
            _make_enh(702, "Conteúdo em japonês", "Adicionar conteúdo em japonês na fonte.", related_ext="MultiSite", created_at="2026-11-02T10:00:00Z"),
        ],
        702, ("no_match",), False,
    ),
    (
        "en_14_login_vs_two_factor_distinct_description",
        [
            _make_enh(701, "Login básico", "Permitir usuário entrar com email e senha básicos na extensão.", related_ext="AuthSite"),
            _make_enh(702, "Importação de cookies e bypass cloudflare", "Contornar Cloudflare usando custom DNS e WebView.", related_ext="AuthSite", created_at="2026-11-02T10:00:00Z"),
        ],
        702, ("no_match",), False,
    ),
])
def test_11_5_enhancement_adversarial_matrix(case_id, issues, target_num, allowed_cls, exp_close):
    dec = _eval(issues, target_num)
    assert dec.classification in allowed_cls, f"{case_id}: got {dec.classification}"
    assert dec.should_auto_close is exp_close


# ===========================================================================
# 11.6 SEGURANÇA, CONCORRÊNCIA, COMENTÁRIOS, CANÔNICOS E OPERAÇÃO (25 cenários)
# ===========================================================================

def test_11_6_sec_01_edit_before_analysis_prevents_close(tmp_path, capsys):
    i1 = _make_ne(10, "PreEdit", "https://preedit.com")
    # Edited before analysis starts to another site/language
    i2_edited = _make_ne(11, "AnotherSource", "https://anothersource.org", created_at="2026-11-02T10:00:00Z")
    api = InMemoryGitHubApi([i1, i2_edited])
    rc = run_cli(["--mode", "single", "--issue-number", "11"], client_override=api)
    assert rc == 0
    _ = capsys.readouterr()
    assert api.issues[11]["state"] == "open"
    assert len(api.writes) == 0


def test_11_6_sec_02_edit_during_analysis_revalidated_before_apply():
    i1 = _make_ne(10, "LiveSite", "https://livesite.com")
    i2_dup = _make_ne(11, "LiveSite", "https://livesite.com", created_at="2026-11-02T10:00:00Z")
    cfg = _cfg()
    parsed = parse_corpus([i1, i2_dup])
    matcher = DuplicateMatcher(parsed, cfg)
    stale_dec = matcher.evaluate(parsed[1])
    assert stale_dec.should_auto_close is True

    # Right before executor.apply(), author edits #11 to another domain!
    api = InMemoryGitHubApi([i1, i2_dup])
    api.issues[11]["body"] = _make_ne(11, "DifferentSite", "https://different.org", created_at="2026-11-02T10:00:00Z").body
    api.issues[11]["title"] = "[Nova extensão]: DifferentSite"

    res = GuardActionExecutor(api, cfg, {10: i1, 11: i2_dup}, matcher=matcher).apply(stale_dec)
    assert res.closed is False
    assert res.decision == "no_match"
    assert api.issues[11]["state"] == "open"
    assert "Duplicado" not in {l["name"] for l in api.issues[11]["labels"]}


def test_11_6_sec_03_edit_without_matcher_aborts_on_stale_fingerprint():
    i1 = _make_ne(10, "NoMatchRef", "https://nomatchref.com")
    i2_dup = _make_ne(11, "NoMatchRef", "https://nomatchref.com", created_at="2026-11-02T10:00:00Z")
    cfg = _cfg()
    stale_dec = _eval([i1, i2_dup], 11)

    api = InMemoryGitHubApi([i1, i2_dup])
    api.issues[11]["body"] = _make_ne(11, "NoMatchRef", "https://nomatchref.com", notes="Agora preciso de recurso adicional de login.").body
    res = GuardActionExecutor(api, cfg, {10: i1, 11: i2_dup}).apply(stale_dec)
    assert res.closed is False
    assert res.skipped_reason == "target_issue_edited_before_apply"
    assert api.issues[11]["state"] == "open"


def test_11_6_sec_04_edit_after_comment_and_before_label_aborts():
    i1 = _make_ne(10, "MidEdit", "https://midedit.com")
    i2 = _make_ne(11, "MidEdit", "https://midedit.com", created_at="2026-11-02T10:00:00Z")
    cfg = _cfg()
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, cfg)
    dec = matcher.evaluate(parsed[1])

    class EditAfterCommentApi(InMemoryGitHubApi):
        def create_comment(self, issue_number: int, body: str) -> dict:
            res = super().create_comment(issue_number, body)
            if issue_number == 11:
                self.issues[11]["title"] = "[Nova extensão]: ChangedDomain"
                self.issues[11]["body"] = _make_ne(11, "ChangedDomain", "https://changeddomain.net").body
            return res

    api = EditAfterCommentApi([i1, i2])
    res = GuardActionExecutor(api, cfg, {10: i1, 11: i2}, matcher=matcher).apply(dec)
    assert res.action_taken == "aborted_concurrent_edit_before_label"
    assert res.closed is False
    assert api.issues[11]["state"] == "open"
    assert "Duplicado" not in {l["name"] for l in api.issues[11]["labels"]}
    meta = parse_marker(api.comments[11][0]["body"])
    assert meta is not None and meta["action"] == "cleared"


def test_11_6_sec_05_edit_after_label_and_before_close_rolls_back_label():
    i1 = _make_ne(10, "PreCloseEdit", "https://precloseedit.com")
    i2 = _make_ne(11, "PreCloseEdit", "https://precloseedit.com", created_at="2026-11-02T10:00:00Z")
    cfg = _cfg()
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, cfg)
    dec = matcher.evaluate(parsed[1])

    class EditAfterLabelApi(InMemoryGitHubApi):
        def add_labels(self, issue_number: int, labels: list[str]) -> None:
            super().add_labels(issue_number, labels)
            if issue_number == 11 and "Duplicado" in labels:
                # Author edits body into a distinct language enhancement right before close_issue()
                self.issues[11]["body"] = _make_ne(
                    11, "PreCloseEdit", "https://precloseedit.com", lang="Espanhol"
                ).body

    api = EditAfterLabelApi([i1, i2])
    res = GuardActionExecutor(api, cfg, {10: i1, 11: i2}, matcher=matcher).apply(dec)
    assert res.action_taken == "aborted_concurrent_edit_before_close"
    assert res.closed is False
    assert api.issues[11]["state"] == "open"
    assert "Duplicado" not in {l["name"] for l in api.issues[11]["labels"]}
    assert "Em análise" in {l["name"] for l in api.issues[11]["labels"]}


def test_11_6_sec_06_comment_added_between_label_and_close_rolls_back_label():
    i1 = _make_bug(10, "Sushi Manga", "Erro No pages found ao abrir capítulo 12 da obra ABC.", part="Leitor / Imagens", version="1.4.2")
    i2 = _make_bug(11, "Sushi Manga", "Ao tentar ler capítulo 12 da obra ABC dá No pages found.", part="Leitor / Imagens", version="1.4.2", created_at="2026-11-02T10:00:00Z", author="alice")
    cfg = _cfg()
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, cfg)
    dec = matcher.evaluate(parsed[1])

    class AuthorCommentAfterLabelApi(InMemoryGitHubApi):
        def add_labels(self, issue_number: int, labels: list[str]) -> None:
            super().add_labels(issue_number, labels)
            if issue_number == 11:
                self.comments.setdefault(11, []).append({
                    "id": 9901,
                    "user": {"login": "alice", "type": "User"},
                    "author_association": "NONE",
                    "body": "Esclarecendo: não é o mesmo problema do ticket #10. O meu acontece somente ao baixar capítulos.",
                })

    api = AuthorCommentAfterLabelApi([i1, i2])
    res = GuardActionExecutor(api, cfg, {10: i1, 11: i2}, matcher=matcher).apply(dec)
    assert res.action_taken == "aborted_concurrent_edit_before_close"
    assert res.closed is False
    assert api.issues[11]["state"] == "open"
    assert "Duplicado" not in {l["name"] for l in api.issues[11]["labels"]}


def test_11_6_sec_07_concurrent_bot_execution_detected_aborts_second_worker():
    i1 = _make_ne(10, "RaceSite", "https://racesite.com")
    i2 = _make_ne(11, "RaceSite", "https://racesite.com", created_at="2026-11-02T10:00:00Z")
    cfg = _cfg()
    parsed = parse_corpus([i1, i2])
    matcher = DuplicateMatcher(parsed, cfg)
    dec = matcher.evaluate(parsed[1])

    class ConcurrentBotApi(InMemoryGitHubApi):
        def add_labels(self, issue_number: int, labels: list[str]) -> None:
            super().add_labels(issue_number, labels)
            if issue_number == 11:
                # Another parallel job (e.g. reconcile vs single) already wrote its auto_closed marker
                self.comments.setdefault(11, []).append({
                    "id": 8888,
                    "user": {"login": "github-actions[bot]", "type": "Bot"},
                    "body": f"<!-- nox-duplicate-guard:v1 issue=11 decision=confirmed_duplicate canonical=10 action=auto_closed fp={dec.fingerprint} -->",
                })

    api = ConcurrentBotApi([i1, i2])
    res = GuardActionExecutor(api, cfg, {10: i1, 11: i2}, matcher=matcher).apply(dec)
    assert res.action_taken == "aborted_concurrent_edit_before_close"
    assert res.closed is False
    assert res.skipped_reason == "concurrent_bot_execution_detected"


def _bug_pair_for_comment_tests():
    canon_bug = _make_bug(500, "Sushi Manga", "No pages found no capítulo 12 da obra ABC no leitor.", part="Leitor / Imagens", version="1.4.2")
    dup_bug = _make_bug(501, "Sushi Manga", "Ao abrir capítulo 12 da obra ABC no leitor aparece No pages found.", part="Leitor / Imagens", version="1.4.2", created_at="2026-11-02T10:00:00Z", author="real_reporter")
    parsed_bug = parse_corpus([canon_bug, dup_bug])
    matcher_bug = DuplicateMatcher(parsed_bug, _cfg())
    dec_bug = matcher_bug.evaluate(parsed_bug[1])
    assert dec_bug.should_auto_close is True
    return canon_bug, dup_bug, matcher_bug, dec_bug


def test_11_6_sec_08_author_comment_denies_duplication_blocks_close():
    canon_bug, dup_bug, matcher_bug, dec_bug = _bug_pair_for_comment_tests()
    api = InMemoryGitHubApi([canon_bug, dup_bug], initial_comments={
        501: [{
            "id": 1001,
            "user": {"login": "real_reporter", "type": "User"},
            "author_association": "NONE",
            "body": "Esclarecendo: não é o mesmo problema do ticket #500. O meu acontece somente ao baixar capítulos.",
        }]
    })
    res = GuardActionExecutor(api, _cfg(), {500: canon_bug, 501: dup_bug}, matcher=matcher_bug).apply(dec_bug)
    assert res.closed is False and res.decision == "possible_duplicate" and api.issues[501]["state"] == "open"


def test_11_6_sec_09_author_comment_contradictory_part_blocks_close():
    canon_bug, dup_bug, matcher_bug, dec_bug = _bug_pair_for_comment_tests()
    api = InMemoryGitHubApi([canon_bug, dup_bug], initial_comments={
        501: [{
            "id": 1002,
            "user": {"login": "real_reporter", "type": "User"},
            "author_association": "NONE",
            "body": "Complementando: na verdade só acontece ao fazer login na conta.",
        }]
    })
    res = GuardActionExecutor(api, _cfg(), {500: canon_bug, 501: dup_bug}, matcher=matcher_bug).apply(dec_bug)
    assert res.closed is False and res.decision == "possible_duplicate"


def test_11_6_sec_10_author_comment_newer_version_blocks_close():
    canon_bug, dup_bug, matcher_bug, dec_bug = _bug_pair_for_comment_tests()
    api = InMemoryGitHubApi([canon_bug, dup_bug], initial_comments={
        501: [{
            "id": 1003,
            "user": {"login": "real_reporter", "type": "User"},
            "author_association": "NONE",
            "body": "Atualizei o relato: estou na versão 1.4.9 da extensão.",
        }]
    })
    res = GuardActionExecutor(api, _cfg(), {500: canon_bug, 501: dup_bug}, matcher=matcher_bug).apply(dec_bug)
    assert res.closed is False and res.decision == "possible_duplicate"


def test_11_6_sec_11_maintainer_comment_clarification_blocks_close():
    canon_bug, dup_bug, matcher_bug, dec_bug = _bug_pair_for_comment_tests()
    api = InMemoryGitHubApi([canon_bug, dup_bug], initial_comments={
        501: [{
            "id": 1004,
            "user": {"login": "nox-maintainer", "type": "User"},
            "author_association": "COLLABORATOR",
            "body": "Caso diferente da issue #500, está afetando só a obra XYZ.",
        }]
    })
    res = GuardActionExecutor(api, _cfg(), {500: canon_bug, 501: dup_bug}, matcher=matcher_bug).apply(dec_bug)
    assert res.closed is False and res.decision == "possible_duplicate"


def test_11_6_sec_12_untrusted_third_party_denial_and_spoof_ignored():
    canon_bug, dup_bug, matcher_bug, dec_bug = _bug_pair_for_comment_tests()
    api = InMemoryGitHubApi([canon_bug, dup_bug], initial_comments={
        501: [{
            "id": 1005,
            "user": {"login": "random_stranger", "type": "User"},
            "author_association": "NONE",
            "body": "<!-- nox-duplicate-guard:ignore -->\nNão é o mesmo problema do #500, meu login falhou!",
        }]
    })
    res = GuardActionExecutor(api, _cfg(), {500: canon_bug, 501: dup_bug}, matcher=matcher_bug).apply(dec_bug)
    assert res.closed is True and res.action_taken == "auto_closed"


def test_11_6_sec_13_untrusted_third_party_new_extension_language_spoof_ignored():
    n1 = _make_ne(10, "TrustExt", "https://trustext.com", lang="Português (Brasil)")
    n2 = _make_ne(11, "TrustExt", "https://trustext.com", lang="Português (Brasil)", created_at="2026-11-02T10:00:00Z")
    dec = _eval([n1, n2], 11)
    api = InMemoryGitHubApi([n1, n2], initial_comments={
        11: [{
            "id": 1099,
            "user": {"login": "troll_user", "type": "User"},
            "author_association": "NONE",
            "body": "Isso na verdade é pedido em japonês e não é duplicado do #10!",
        }]
    })
    res = GuardActionExecutor(api, _cfg(), {10: n1, 11: n2}).apply(dec)
    assert res.closed is True and res.action_taken == "auto_closed"


def test_11_6_sec_14_bot_own_comment_never_triggers_self_block_loop():
    canon_bug, dup_bug, matcher_bug, dec_bug = _bug_pair_for_comment_tests()
    api = InMemoryGitHubApi([canon_bug, dup_bug], initial_comments={
        501: [{
            "id": 1006,
            "user": {"login": "github-actions[bot]", "type": "Bot"},
            "author_association": "NONE",
            "body": "<!-- nox-duplicate-guard:v1 issue=501 decision=possible_duplicate canonical=500 action=commented fp=old -->\nNão é o mesmo caso? Permanece aberto.",
        }]
    })
    res = GuardActionExecutor(api, _cfg(), {500: canon_bug, 501: dup_bug}, matcher=matcher_bug).apply(dec_bug)
    assert res.closed is True and res.action_taken == "auto_closed"


def test_11_6_sec_15_asymmetry_comment_never_promotes_ambiguous_to_auto_close():
    g1 = _make_bug(600, "Sushi Manga", "Sushi Manga não está funcionando. Aparece HTTP 400.", part="Outro", version="", author="u1")
    g2 = _make_bug(601, "Sushi Manga", "Sushi Manga não funciona. Erro HTTP 400.", part="Outro", version="", created_at="2026-11-02T10:00:00Z", author="u2")
    dec_g = _eval([g1, g2], 601)
    assert dec_g.classification == "possible_duplicate"
    api = InMemoryGitHubApi([g1, g2], initial_comments={
        601: [{
            "id": 1007,
            "user": {"login": "u2", "type": "User"},
            "author_association": "NONE",
            "body": "Aliás, também dá No pages found no capítulo 12 da obra ABC na versão 1.4.2.",
        }]
    })
    res = GuardActionExecutor(api, _cfg(), {600: g1, 601: g2}).apply(dec_g)
    assert res.closed is False and res.decision == "possible_duplicate"


def test_11_6_ops_16_chain_of_five_resolves_to_oldest():
    c10 = _make_ne(10, "Chain5", "https://chain5.com", created_at="2026-11-01T10:00:00Z")
    c11 = _make_ne(11, "Chain5", "https://chain5.com", state="closed", state_reason="duplicate", labels=["Nova extensão", "Mangá", "Duplicado"], created_at="2026-11-02T10:00:00Z")
    c12 = _make_ne(12, "Chain5", "https://chain5.com", state="closed", state_reason="duplicate", labels=["Nova extensão", "Mangá", "Duplicado"], created_at="2026-11-03T10:00:00Z")
    c13 = _make_ne(13, "Chain5", "https://chain5.com", state="closed", state_reason="duplicate", labels=["Nova extensão", "Mangá", "Duplicado"], created_at="2026-11-04T10:00:00Z")
    c14 = _make_ne(14, "Chain5", "https://chain5.com", created_at="2026-11-05T10:00:00Z")
    dec = _eval([c10, c11, c12, c13, c14], 14, dup_links={13: 12, 12: 11, 11: 10})
    assert dec.canonical_issue == 10 and dec.should_auto_close is True


def test_11_6_ops_17_cycle_two_and_three_nodes_blocked():
    cyc10 = _make_ne(10, "CycleSite", "https://cyclesite.com", labels=["Nova extensão", "Mangá", "Duplicado"], created_at="2026-11-01T10:00:00Z")
    cyc11 = _make_ne(11, "CycleSite", "https://cyclesite.com", labels=["Nova extensão", "Mangá", "Duplicado"], created_at="2026-11-02T10:00:00Z")
    cyc12 = _make_ne(12, "CycleSite", "https://cyclesite.com", created_at="2026-11-03T10:00:00Z")
    dec2 = _eval([cyc10, cyc11, cyc12], 12, dup_links={11: 10, 10: 11})
    assert dec2.should_auto_close is False and dec2.classification == "possible_duplicate"
    cyc12_dup = _make_ne(12, "CycleSite", "https://cyclesite.com", state="closed", state_reason="duplicate", labels=["Nova extensão", "Mangá", "Duplicado"], created_at="2026-11-03T10:00:00Z")
    cyc13 = _make_ne(13, "CycleSite", "https://cyclesite.com", created_at="2026-11-04T10:00:00Z")
    dec3 = _eval([cyc10, cyc11, cyc12_dup, cyc13], 13, dup_links={12: 11, 11: 10, 10: 12})
    assert dec3.should_auto_close is False


def test_11_6_ops_18_canonical_declined_not_planned_blocks_auto_close():
    decl10 = _make_ne(10, "DeclinedSite", "https://declined.com", state="closed", state_reason="not_planned", labels=["Nova extensão", "Mangá", "Recusado"])
    decl11 = _make_ne(11, "DeclinedSite", "https://declined.com", created_at="2026-11-02T10:00:00Z")
    assert _eval([decl10, decl11], 11).should_auto_close is False


def test_11_6_ops_19_canonical_edited_mid_run_aborts_auto_close():
    ed10 = _make_ne(10, "CanonEdit", "https://canonedit.com")
    ed11 = _make_ne(11, "CanonEdit", "https://canonedit.com", created_at="2026-11-02T10:00:00Z")
    parsed_ed = parse_corpus([ed10, ed11])
    matcher_ed = DuplicateMatcher(parsed_ed, _cfg())
    dec_ed = matcher_ed.evaluate(parsed_ed[1])
    api = InMemoryGitHubApi([ed10, ed11])
    api.issues[10]["body"] = _make_ne(10, "DifferentOrg", "https://differentorg.org").body
    res = GuardActionExecutor(api, _cfg(), {10: ed10, 11: ed11}, matcher=matcher_ed).apply(dec_ed)
    assert res.closed is False and api.issues[11]["state"] == "open"


def test_11_6_ops_20_canonical_edited_between_label_and_close_rolls_back():
    ed10 = _make_ne(10, "CanonRace", "https://canonrace.com")
    ed11 = _make_ne(11, "CanonRace", "https://canonrace.com", created_at="2026-11-02T10:00:00Z")
    parsed_ed = parse_corpus([ed10, ed11])
    matcher_ed = DuplicateMatcher(parsed_ed, _cfg())
    dec_ed = matcher_ed.evaluate(parsed_ed[1])

    class CanonEditBeforeCloseApi(InMemoryGitHubApi):
        def add_labels(self, issue_number: int, labels: list[str]) -> None:
            super().add_labels(issue_number, labels)
            if issue_number == 11:
                self.issues[10]["state"] = "closed"
                self.issues[10]["state_reason"] = "not_planned"

    api = CanonEditBeforeCloseApi([ed10, ed11])
    res = GuardActionExecutor(api, _cfg(), {10: ed10, 11: ed11}, matcher=matcher_ed).apply(dec_ed)
    assert res.action_taken == "aborted_concurrent_edit_before_close"
    assert res.closed is False and "Duplicado" not in {l["name"] for l in api.issues[11]["labels"]}


def test_11_6_ops_21_canonical_deleted_404_blocks_auto_close():
    ed10 = _make_ne(10, "MissingCanon", "https://missingcanon.com")
    ed11 = _make_ne(11, "MissingCanon", "https://missingcanon.com", created_at="2026-11-02T10:00:00Z")
    parsed_ed = parse_corpus([ed10, ed11])
    matcher_ed = DuplicateMatcher(parsed_ed, _cfg())
    dec_ed = matcher_ed.evaluate(parsed_ed[1])
    api_404 = InMemoryGitHubApi([ed10, ed11])
    orig_get = api_404.get_issue

    def get_with_404(num: int) -> dict:
        if num == 10:
            raise GitHubApiError("Not Found", status=404)
        return orig_get(num)

    api_404.get_issue = get_with_404  # type: ignore[method-assign]
    res = GuardActionExecutor(api_404, _cfg(), {10: ed10, 11: ed11}, matcher=matcher_ed).apply(dec_ed)
    assert res.closed is False and api_404.issues[11]["state"] == "open"


def test_11_6_ops_22_canonical_marked_duplicado_concurrently_blocks_close():
    ed10 = _make_ne(10, "CanonDup", "https://canondup.com")
    ed11 = _make_ne(11, "CanonDup", "https://canondup.com", created_at="2026-11-02T10:00:00Z")
    parsed_ed = parse_corpus([ed10, ed11])
    matcher_ed = DuplicateMatcher(parsed_ed, _cfg())
    dec_ed = matcher_ed.evaluate(parsed_ed[1])
    api = InMemoryGitHubApi([ed10, ed11])
    api.issues[10]["labels"].append({"name": "Duplicado"})
    res = GuardActionExecutor(api, _cfg(), {10: ed10, 11: ed11}, matcher=matcher_ed).apply(dec_ed)
    assert res.closed is False and api.issues[11]["state"] == "open"


def test_11_6_ops_23_cache_invalidated_on_rule_version_upgrade(tmp_path, capsys):
    ed10 = _make_ne(10, "CacheVer", "https://cachever.com")
    ed11 = _make_ne(11, "CacheVer", "https://cachever.com", created_at="2026-11-02T10:00:00Z")
    cache_file = tmp_path / "version_cache.json"
    cache_file.write_text(json.dumps({
        "rule_version": "1.2.0",
        "last_full_backfill_at": "2026-11-10T00:00:00Z",
        "evaluated": {"10": {"updated_at": ed10.updated_at, "fingerprint": "oldfp", "auto_close_enabled": True, "decision": "no_match", "canonical_issue": None, "canonical_state": None, "action_taken": "noop"}},
        "dup_links": {},
        "catalog": {},
    }), encoding="utf-8")
    api = InMemoryGitHubApi([ed10, ed11])
    rc = run_cli(["--mode", "reconcile", "--cache-file", str(cache_file)], client_override=api)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rule_version"] == RULE_ENGINE_VERSION
    assert out["effective_mode"] == "backfill"
    assert out["skipped_cached_count"] == 0


def test_11_6_ops_24_standalone_catalog_fulfilled_uses_concluido_never_duplicado():
    cat = _cat(manga=[{"name": "SoloCat", "pkg": "p.solocat", "lang": "pt-BR", "version": "1.0", "sources": [{"name": "SoloCat", "lang": "pt-BR", "baseUrl": "https://solocat.com"}]}])
    solo_req = _make_ne(99, "SoloCat", "https://solocat.com", lang="Português (Brasil)")
    dec = _eval([solo_req], 99, catalog=cat)
    assert dec.match_kind == "already_fulfilled_creation"
    assert dec.canonical_issue is None
    assert dec.resolution_label == LABEL_DONE
    assert dec.close_state_reason == "completed"
    api = InMemoryGitHubApi([solo_req])
    res = GuardActionExecutor(api, _cfg(), {99: solo_req}).apply(dec)
    assert res.closed is True
    assert "Concluído" in res.labels_added
    assert "Duplicado" not in {l["name"] for l in api.issues[99]["labels"]}


def test_11_6_ops_25_historical_127_and_143_preserved_unmodified_in_backfill(tmp_path, capsys):
    hist_127 = _make_bug(127, "Hist", "Erro", state="closed", state_reason="duplicate", labels=["Problema na extensão", "Mangá", "Concluído"])
    hist_143 = _make_ne(143, "HentaiHaven", "https://hentaihaven.xxx/", ext_type="Anime", state="closed", state_reason="completed", labels=["Anime", "Nova extensão", "Em análise", "Conteúdo +18"])
    api = InMemoryGitHubApi([hist_127, hist_143])
    rc = run_cli(["--mode", "backfill", "--cache-file", str(tmp_path / "hist_cache.json")], client_override=api)
    assert rc == 0
    _ = capsys.readouterr()
    assert len(api.writes) == 0
    assert {l["name"] for l in api.issues[127]["labels"]} == {"Problema na extensão", "Mangá", "Concluído"}
    assert {l["name"] for l in api.issues[143]["labels"]} == {"Anime", "Nova extensão", "Em análise", "Conteúdo +18"}


# ===========================================================================
# 12. CASOS REAIS DO PROJECT NOX + VARIAÇÕES CONTRAFACTUAIS (16 cenários)
# ===========================================================================

@pytest.mark.parametrize("cf_id", [
    "cf_01_140_149_real_open_auto_closes",
    "cf_02_140_done_149_open_without_catalog_preserves",
    "cf_03_149_edited_to_english_no_match",
    "cf_04_141_closed_143_open_with_en_catalog_no_match",
    "cf_05_141_open_143_open_with_en_catalog_no_match",
    "cf_06_143_open_even_with_all_catalog_preserves_enhancement",
    "cf_07_150_154_real_never_auto_closes",
    "cf_08_150_154_two_years_later_never_auto_closes",
    "cf_09_130_153_open_distinct_new_domains_no_match",
    "cf_10_130_closed_153_open_no_match",
    "cf_11_125_151_bug_vs_login_feature_no_match",
    "cf_12_125_edited_to_reader_403_preserves",
    "cf_13_127_simulated_reopened_never_recloses",
    "cf_14_156_newer_version_16_15_preserves",
    "cf_15_156_two_years_later_preserves",
    "cf_16_156_vs_search_bug_no_match",
])
def test_12_real_project_nox_and_counterfactual_scenarios(cf_id):
    real = _load_real()
    cat_hh_en = _cat(anime=[{
        "name": "Aniyomi: HentaiHaven",
        "pkg": "eu.kanade.tachiyomi.animeextension.en.hentaihaven",
        "lang": "en",
        "version": "16.15",
        "sources": [{"name": "HentaiHaven", "lang": "en", "baseUrl": "https://hentaihaven.xxx"}],
    }])
    cat_hh_all = _cat(anime=[{
        "name": "Aniyomi: HentaiHaven",
        "pkg": "eu.kanade.tachiyomi.animeextension.all.hentaihaven",
        "lang": "all",
        "version": "16.20",
        "sources": [{"name": "HentaiHaven", "lang": "all", "baseUrl": "https://hentaihaven.xxx"}],
    }])

    if cf_id == "cf_01_140_149_real_open_auto_closes":
        assert _eval([real[140], real[149]], 149).should_auto_close is True
    elif cf_id == "cf_02_140_done_149_open_without_catalog_preserves":
        r140_done = RawIssue(**{**real[140].__dict__, "state": "closed", "state_reason": "completed", "labels": ("Anime", "Nova extensão", "Concluído", "Conteúdo +18")})
        assert _eval([r140_done, real[149]], 149).should_auto_close is False
    elif cf_id == "cf_03_149_edited_to_english_no_match":
        r149_en = RawIssue(**{**real[149].__dict__, "body": real[149].body.replace("Português (Brasil)", "Inglês")})
        assert _eval([real[140], r149_en], 149).classification == "no_match"
    elif cf_id == "cf_04_141_closed_143_open_with_en_catalog_no_match":
        assert _eval([real[141], real[143]], 143, catalog=cat_hh_en).classification == "no_match"
    elif cf_id == "cf_05_141_open_143_open_with_en_catalog_no_match":
        r141_open = RawIssue(**{**real[141].__dict__, "state": "open", "closed_at": None})
        r143_open = RawIssue(**{**real[143].__dict__, "state": "open", "closed_at": None})
        assert _eval([r141_open, r143_open], 143, catalog=cat_hh_en).classification == "no_match"
    elif cf_id == "cf_06_143_open_even_with_all_catalog_preserves_enhancement":
        r143_open = RawIssue(**{**real[143].__dict__, "state": "open", "closed_at": None})
        assert _eval([r143_open], 143, catalog=cat_hh_all).should_auto_close is False
    elif cf_id == "cf_07_150_154_real_never_auto_closes":
        assert _eval([real[150], real[154]], 154).should_auto_close is False
    elif cf_id == "cf_08_150_154_two_years_later_never_auto_closes":
        r154_2yr = RawIssue(**{**real[154].__dict__, "created_at": "2028-11-17T22:43:20Z"})
        assert _eval([real[150], r154_2yr], 154).should_auto_close is False
    elif cf_id == "cf_09_130_153_open_distinct_new_domains_no_match":
        r130_open = RawIssue(**{**real[130].__dict__, "state": "open", "closed_at": None})
        r153_open = RawIssue(**{**real[153].__dict__, "state": "open", "closed_at": None})
        assert _eval([r130_open, r153_open], 153).classification == "no_match"
    elif cf_id == "cf_10_130_closed_153_open_no_match":
        r153_open = RawIssue(**{**real[153].__dict__, "state": "open", "closed_at": None})
        assert _eval([real[130], r153_open], 153).classification == "no_match"
    elif cf_id == "cf_11_125_151_bug_vs_login_feature_no_match":
        assert _eval([real[125], real[151]], 151).classification == "no_match"
    elif cf_id == "cf_12_125_edited_to_reader_403_preserves":
        r125_dup = make_raw(
            990, real[125].title,
            real[125].body.replace("Metadados", "Leitor / Imagens").replace("HTTP 400", "HTTP 403 no leitor"),
            labels=list(real[125].labels), created_at="2026-11-03T10:00:00Z",
        )
        assert _eval([real[125], r125_dup], 990).should_auto_close is False
    elif cf_id == "cf_13_127_simulated_reopened_never_recloses":
        r127_real_body = (
            "### Tipo de extensão\nMangá\n### Nome da extensão\nHanami Heaven\n"
            "### Versão da extensão\n1.6.60\n### Aplicativo utilizado\nMihon\n"
            "### Parte afetada\nPopular / Recentes\n"
            "### O que está acontecendo?\nNão carrega quando entra, dá esse erro \"HTTP 400\"\n"
            "### Como reproduzir?\nSó abrir\n"
            "### Informações complementares (opcional)\nhttps://hanamiheaven.org/ link do site"
        )
        r127_reopened = make_raw(
            127,
            "[Problema na extensão]: Hanami Heaven — HTTP 400",
            r127_real_body,
            labels=["Problema na extensão", "Mangá", "Em análise"],
            state="open",
            state_reason="reopened",
            created_at="2026-11-03T20:55:50Z",
        )
        dec = _eval([real[125], r127_reopened], 127)
        assert dec.should_auto_close is False
        api = InMemoryGitHubApi([real[125], r127_reopened])
        res = GuardActionExecutor(api, _cfg(), {125: real[125], 127: r127_reopened}).apply(dec)
        assert res.closed is False and api.issues[127]["state"] == "open"
    elif cf_id == "cf_14_156_newer_version_16_15_preserves":
        r156_newer_ver = RawIssue(**{**real[156].__dict__, "number": 991, "body": real[156].body.replace("16.13", "16.15"), "created_at": "2026-11-22T10:00:00Z"})
        assert _eval([real[156], r156_newer_ver], 991).should_auto_close is False
    elif cf_id == "cf_15_156_two_years_later_preserves":
        r156_2yr_later = RawIssue(**{**real[156].__dict__, "number": 992, "created_at": "2028-11-20T10:00:00Z"})
        assert _eval([real[156], r156_2yr_later], 992).should_auto_close is False
    elif cf_id == "cf_16_156_vs_search_bug_no_match":
        r156_search_bug = _make_bug(993, "Animes Digital", "Busca não retorna resultados na pesquisa.", ext_type="Anime", part="Busca / Filtros", version="16.13", created_at="2026-11-21T10:00:00Z")
        assert _eval([real[156], r156_search_bug], 993).classification == "no_match"

