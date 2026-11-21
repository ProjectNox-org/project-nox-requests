# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Labeled benchmark evaluation suite reporting Precision, Recall, FP, FN, and per-category accuracy."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.duplicate_guard.cli import parse_corpus  # noqa: E402
from scripts.duplicate_guard.config import load_config  # noqa: E402
from scripts.duplicate_guard.matcher import DuplicateMatcher  # noqa: E402
from scripts.duplicate_guard.models import RawIssue  # noqa: E402

FIXTURES_PATH = REPO_ROOT / "tests" / "fixtures" / "real_issues_snapshot.json"


def _mk(
    num: int,
    title: str,
    body: str,
    labels: list[str],
    *,
    state: str = "open",
    state_reason: str | None = None,
    created_at: str = "2026-11-01T10:00:00Z",
    closed_at: str | None = None,
) -> RawIssue:
    return RawIssue(
        number=num,
        title=title,
        body=body,
        state=state,
        state_reason=state_reason,
        created_at=created_at,
        updated_at=created_at,
        closed_at=closed_at,
        html_url=f"https://github.com/ProjectNox-org/project-nox-requests/issues/{num}",
        labels=tuple(labels),
        author=f"user{num}",
    )


def _load_real() -> dict[int, RawIssue]:
    raw = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    return {int(item["number"]): RawIssue.from_github_dict(item) for item in raw["issues"]}


def build_labeled_cases():
    """Return labeled benchmark cases across all 5 categories.

    Tuple: (case_id, category, older_issue, newer_issue, expected_auto_close, expected_alert_or_close)
    """
    real = _load_real()
    cases = [
        # --- NEW EXTENSION ---
        (
            "ne_pos_real_140_149", "new_extension", real[140], real[149], True, True
        ),
        (
            "ne_pos_spacing_www", "new_extension",
            _mk(1001, "[Nova extensão]: Manga Livre", "### Tipo de extensão\nMangá\n### Nome da fonte\nManga Livre\n### URL do site\nhttp://mangalivre.example\n", ["Nova extensão", "Mangá"]),
            _mk(1002, "[Nova extensão]: MangaLivre", "### Tipo de extensão\nMangá\n### Nome da fonte\nMangaLivre\n### URL do site\nhttps://www.mangalivre.example/\n", ["Nova extensão", "Mangá"], created_at="2026-11-02T10:00:00Z"),
            True, True,
        ),
        (
            "ne_pos_accent_case", "new_extension",
            _mk(1003, "[Nova extensão]: Suki Mangás", "### Tipo de extensão\nMangá\n### Nome da fonte\nSuki Mangás\n### URL do site\nhttps://sukimangas.com\n", ["Nova extensão", "Mangá"]),
            _mk(1004, "[Nova extensão]: sukimangas", "### Tipo de extensão\nMangá\n### Nome da fonte\nsukimangas\n### URL do site\nhttps://sukimangas.com/home\n", ["Nova extensão", "Mangá"], created_at="2026-11-02T10:00:00Z"),
            True, True,
        ),
        (
            "ne_pos_anime_dup", "new_extension",
            _mk(1005, "[Nova extensão]: OtakuVision", "### Tipo de extensão\nAnime\n### Nome da fonte\nOtakuVision\n### URL do site\nhttps://otakuvision.tv\n", ["Nova extensão", "Anime"]),
            _mk(1006, "[Nova extensão]: Otaku Vision", "### Tipo de extensão\nAnime\n### Nome da fonte\nOtaku Vision\n### URL do site\nhttps://www.otakuvision.tv\n", ["Nova extensão", "Anime"], created_at="2026-11-02T10:00:00Z"),
            True, True,
        ),
        (
            "ne_neg_manga_vs_anime", "new_extension",
            _mk(1007, "[Nova extensão]: YoruPortal", "### Tipo de extensão\nMangá\n### Nome da fonte\nYoruPortal\n### URL do site\nhttps://yoruportal.com\n", ["Nova extensão", "Mangá"]),
            _mk(1008, "[Nova extensão]: YoruPortal", "### Tipo de extensão\nAnime\n### Nome da fonte\nYoruPortal\n### URL do site\nhttps://yoruportal.com\n", ["Nova extensão", "Anime"], created_at="2026-11-02T10:00:00Z"),
            False, False,
        ),
        (
            "ne_neg_real_animex_vs_animes", "new_extension",
            _mk(1009, real[3].title, real[3].body, ["Nova extensão", "Anime"]),
            _mk(1010, real[7].title, real[7].body, ["Nova extensão", "Anime"], created_at="2026-11-02T10:00:00Z"),
            False, False,
        ),
        (
            "ne_neg_shared_host_diff_sub", "new_extension",
            _mk(1011, "[Nova extensão]: ScanOne", "### Tipo de extensão\nMangá\n### Nome da fonte\nScanOne\n### URL do site\nhttps://scanone.github.io\n", ["Nova extensão", "Mangá"]),
            _mk(1012, "[Nova extensão]: ScanTwo", "### Tipo de extensão\nMangá\n### Nome da fonte\nScanTwo\n### URL do site\nhttps://scantwo.github.io\n", ["Nova extensão", "Mangá"], created_at="2026-11-02T10:00:00Z"),
            False, False,
        ),
        (
            "ne_neg_different_domains_same_name_possible_only", "new_extension",
            _mk(1013, "[Nova extensão]: MoonScan", "### Tipo de extensão\nMangá\n### Nome da fonte\nMoonScan\n### URL do site\nhttps://moonscan.com\n", ["Nova extensão", "Mangá"]),
            _mk(1014, "[Nova extensão]: MoonScan", "### Tipo de extensão\nMangá\n### Nome da fonte\nMoonScan\n### URL do site\nhttps://moonscan.org\n", ["Nova extensão", "Mangá"], created_at="2026-11-02T10:00:00Z"),
            False, True,
        ),
        (
            "ne_neg_closed_declined_real_29", "new_extension",
            real[29],
            _mk(1015, "[Nova extensão]: Sakura", "### Tipo de extensão\nMangá\n### Nome da fonte\nSakura\n### URL do site\nhttps://sakuramangas.org/\n", ["Nova extensão", "Mangá"], created_at="2026-11-18T10:00:00Z"),
            False, True,
        ),

        # --- BUG ---
        (
            "bug_pos_real_132_no_pages", "bug",
            real[132],
            _mk(2001, "[Problema na extensão]: Bakai — Erro No pages found no leitor", "### Tipo de extensão\nMangá\n### Nome da extensão\nBakai\n### Versão da extensão\n1.6.21\n### Aplicativo utilizado\nKomikku\n### Parte afetada\nLeitor / Imagens\n### O que está acontecendo?\nAo tentar ler qualquer capítulo dá No pages found.\n", ["Problema na extensão", "Mangá"], created_at="2026-11-08T10:00:00Z"),
            True, True,
        ),
        (
            "bug_pos_taimu_nxdomain", "bug",
            _mk(2002, real[52].title, real[52].body, ["Problema na extensão", "Mangá"]),
            _mk(2003, real[55].title, real[55].body, ["Problema na extensão", "Mangá"], created_at="2026-11-02T10:00:00Z"),
            True, True,
        ),
        (
            "bug_pos_json_decode_identical", "bug",
            real[128],
            _mk(2004, "[Problema na extensão]: Redetoons — Erro JSON na lista", "### Tipo de extensão\nAnime\n### Nome da extensão\nRedetoons\n### Versão da extensão\n14.5\n### Aplicativo utilizado\nAniyomi\n### Parte afetada\nPopular / Recentes\n### O que está acontecendo?\nJsonDecodingException: Unexpected JSON token at offset 0: Expected start of the object '{', but had '<' instead JSON input: <!DOCTYPE html>\n", ["Problema na extensão", "Anime"], created_at="2026-11-05T10:00:00Z"),
            True, True,
        ),
        (
            "bug_neg_http404_generic_argos_possible_only", "bug",
            real[146],
            _mk(2005, "[Problema na extensão]: Argos Comics — Erro 404 nos capítulos", "### Tipo de extensão\nMangá\n### Nome da extensão\nArgos Comics\n### Versão da extensão\n1.6.65\n### Aplicativo utilizado\nMihon\n### Parte afetada\nOutro\n### O que está acontecendo?\nAo tentar olhar capítulos de qualquer obra ocorre erro HTTP 404.\n", ["Problema na extensão", "Mangá"], created_at="2026-11-12T10:00:00Z"),
            False, True,
        ),
        (
            "bug_pos_sushi_specific_no_pages", "bug",
            _mk(304, "[Problema na extensão]: Sushi Manga — Erro No pages found no capítulo 12", "### Tipo de extensão\nMangá\n### Nome da extensão\nSushi Manga\n### Versão da extensão\n1.4.2\n### Aplicativo utilizado\nMihon\n### Parte afetada\nLeitor / Imagens\n### O que está acontecendo?\nErro No pages found no capítulo 12 da obra ABC no leitor.\n", ["Problema na extensão", "Mangá"], created_at="2026-11-10T10:00:00Z"),
            _mk(305, "[Problema na extensão]: Sushi Manga — No pages found capítulo 12 obra ABC", "### Tipo de extensão\nMangá\n### Nome da extensão\nSushi Manga\n### Versão da extensão\n1.4.2\n### Aplicativo utilizado\nMihon\n### Parte afetada\nLeitor / Imagens\n### O que está acontecendo?\nAo abrir o capítulo 12 da obra ABC aparece No pages found.\n", ["Problema na extensão", "Mangá"], created_at="2026-11-11T10:00:00Z"),
            True, True,
        ),
        (
            "bug_neg_sushi_403_reader_vs_login", "bug",
            _mk(300, "[Problema na extensão]: Sushi Manga — HTTP 403 no leitor", "### Tipo de extensão\nMangá\n### Nome da extensão\nSushi Manga\n### Parte afetada\nOutro\n### O que está acontecendo?\nExtensão Sushi Manga apresenta HTTP 403 quando abre o leitor.\n", ["Problema na extensão", "Mangá"], created_at="2026-11-10T10:00:00Z"),
            _mk(301, "[Problema na extensão]: Sushi Manga — HTTP 403 no login", "### Tipo de extensão\nMangá\n### Nome da extensão\nSushi Manga\n### Parte afetada\nOutro\n### O que está acontecendo?\nExtensão Sushi Manga apresenta HTTP 403 quando tenta fazer login.\n", ["Problema na extensão", "Mangá"], created_at="2026-11-11T10:00:00Z"),
            False, False,
        ),
        (
            "bug_neg_sushi_generic_http400_possible_only", "bug",
            _mk(302, "[Problema na extensão]: Sushi Manga — HTTP 400", "### Tipo de extensão\nMangá\n### Nome da extensão\nSushi Manga\n### O que está acontecendo?\nSushi Manga não está funcionando. Aparece HTTP 400.\n", ["Problema na extensão", "Mangá"], created_at="2026-11-10T10:00:00Z"),
            _mk(303, "[Problema na extensão]: Sushi Manga — Erro HTTP 400", "### Tipo de extensão\nMangá\n### Nome da extensão\nSushi Manga\n### O que está acontecendo?\nSushi Manga não funciona. Erro HTTP 400.\n", ["Problema na extensão", "Mangá"], created_at="2026-11-11T10:00:00Z"),
            False, True,
        ),
        (
            "bug_neg_login_vs_reader", "bug",
            real[132],
            _mk(2006, "[Problema na extensão]: Bakai — Erro de login", "### Tipo de extensão\nMangá\n### Nome da extensão\nBakai\n### Versão da extensão\n1.6.21\n### Aplicativo utilizado\nMihon\n### Parte afetada\nLogin\n### O que está acontecendo?\nErro ao autenticar conta na extensão Bakai.\n", ["Problema na extensão", "Mangá"], created_at="2026-11-08T10:00:00Z"),
            False, False,
        ),
        (
            "bug_neg_search_vs_video", "bug",
            real[156],
            _mk(2007, "[Problema na extensão]: Animes Digital — Busca quebrada", "### Tipo de extensão\nAnime\n### Nome da extensão\nAnimes Digital\n### Versão da extensão\n14.2\n### Aplicativo utilizado\nAniyomi\n### Parte afetada\nBusca / Filtros\n### O que está acontecendo?\nPesquisa por nome não retorna animes.\n", ["Problema na extensão", "Anime"], created_at="2026-11-21T10:00:00Z"),
            False, False,
        ),
        (
            "bug_neg_real_150_154_possible_only", "bug",
            real[150], real[154], False, True,
        ),
        (
            "bug_neg_regression_after_fix", "bug",
            _mk(2008, "[Problema na extensão]: Bakai — No pages found", "### Tipo de extensão\nMangá\n### Nome da extensão\nBakai\n### Versão da extensão\n1.6.10\n### Parte afetada\nLeitor / Imagens\n### O que está acontecendo?\nNo pages found.\n", ["Problema na extensão", "Mangá", "Concluído"], state="closed", state_reason="completed", created_at="2026-10-01T10:00:00Z", closed_at="2026-10-05T10:00:00Z"),
            _mk(2009, "[Problema na extensão]: Bakai — No pages found", "### Tipo de extensão\nMangá\n### Nome da extensão\nBakai\n### Versão da extensão\n1.6.30\n### Parte afetada\nLeitor / Imagens\n### O que está acontecendo?\nNo pages found.\n", ["Problema na extensão", "Mangá"], created_at="2026-11-05T10:00:00Z"),
            False, False,
        ),
        (
            "bug_neg_insufficient_info", "bug",
            _mk(2010, "[Problema na extensão]: Fliptru", "### Tipo de extensão\nMangá\n### Nome da extensão\nFliptru\n### Parte afetada\nOutro\n### O que está acontecendo?\nnão funciona\n", ["Problema na extensão", "Mangá"]),
            _mk(2011, "[Problema na extensão]: Fliptru", "### Tipo de extensão\nMangá\n### Nome da extensão\nFliptru\n### Parte afetada\nOutro\n### O que está acontecendo?\nerro\n", ["Problema na extensão", "Mangá"], created_at="2026-11-02T10:00:00Z"),
            False, False,
        ),
        (
            "bug_neg_different_http_codes", "bug",
            real[145],
            _mk(2012, "[Problema na extensão]: Blackout Comics — HTTP 404", "### Tipo de extensão\nMangá\n### Nome da extensão\nBlackout Comics\n### Versão da extensão\n1.6.12\n### Parte afetada\nPopular / Recentes\n### O que está acontecendo?\nTodas as páginas retornam HTTP 404 Not Found.\n", ["Problema na extensão", "Mangá"], created_at="2026-11-12T10:00:00Z"),
            False, False,
        ),

        # --- DOMAIN CHANGE ---
        (
            "dc_pos_exact_transition", "domain_change",
            _mk(3001, "[Mudança de domínio]: CineVEO", "### Tipo de extensão\nAnime\n### Nome da extensão\nCineVEO\n### URL antiga\nhttps://cineveo.lat\n### URL nova\nhttps://cineveo.cfd/\n", ["Mudança de domínio", "Anime"]),
            _mk(3002, "[Mudança de domínio]: CineVEO", "### Tipo de extensão\nAnime\n### Nome da extensão\nCineVEO\n### URL antiga\nhttp://www.cineveo.lat/\n### URL nova\nhttps://www.cineveo.cfd\n", ["Mudança de domínio", "Anime"], created_at="2026-11-02T10:00:00Z"),
            True, True,
        ),
        (
            "dc_pos_manga_exact", "domain_change",
            _mk(3003, "[Mudança de domínio]: MangaChan", "### Tipo de extensão\nMangá\n### Nome da extensão\nMangaChan\n### URL antiga\nhttps://mangachan.net\n### URL nova\nhttps://mangachan.org\n", ["Mudança de domínio", "Mangá"]),
            _mk(3004, "[Mudança de domínio]: Manga Chan", "### Tipo de extensão\nMangá\n### Nome da extensão\nManga Chan\n### URL antiga\nhttps://mangachan.net/\n### URL nova\nhttps://mangachan.org/\n", ["Mudança de domínio", "Mangá"], created_at="2026-11-02T10:00:00Z"),
            True, True,
        ),
        (
            "dc_neg_real_130_153_fleur_blanche", "domain_change",
            _mk(130, real[130].title, real[130].body, ["Mudança de domínio", "Mangá"], created_at=real[130].created_at),
            _mk(153, real[153].title, real[153].body, ["Mudança de domínio", "Mangá"], created_at=real[153].created_at),
            False, False,
        ),
        (
            "dc_neg_real_13_54_animefire", "domain_change",
            _mk(13, real[13].title, real[13].body, ["Mudança de domínio", "Anime"], created_at=real[13].created_at),
            _mk(54, real[54].title, real[54].body, ["Mudança de domínio", "Anime"], created_at=real[54].created_at),
            False, False,
        ),
        (
            "dc_neg_real_103_137_redetoons", "domain_change",
            _mk(103, real[103].title, real[103].body, ["Mudança de domínio", "Anime"], created_at=real[103].created_at),
            real[137],
            False, False,
        ),
        (
            "dc_neg_partial_old_url_possible_only", "domain_change",
            real[138],
            _mk(3005, "[Mudança de domínio]: CineVEO", "### Tipo de extensão\nAnime\n### Nome da extensão\nCineVEO\n### URL antiga\ncineveo.old\n### URL nova\nhttps://cineveo.cfd\n", ["Mudança de domínio", "Anime"], created_at="2026-11-10T10:00:00Z"),
            False, True,
        ),

        # --- DEAD SOURCE ---
        (
            "ds_pos_permanent_same_window", "dead_source",
            _mk(4001, "[Fonte indisponível]: GhostScan", "### Tipo de extensão\nMangá\n### Nome da extensão\nGhostScan\n### O que aconteceu?\nO domínio expirou há semanas e o Discord confirmou encerramento definitivo.\n### Informações complementares (opcional)\nhttps://ghostscan.xyz retornando NXDOMAIN.\n", ["Fonte indisponível", "Mangá"], created_at="2026-11-01T10:00:00Z"),
            _mk(4002, "[Fonte indisponível]: Ghost Scan", "### Tipo de extensão\nMangá\n### Nome da extensão\nGhost Scan\n### O que aconteceu?\nDomínio expirado há semanas, equipe confirmou encerramento definitivo no anúncio oficial.\n### Informações complementares (opcional)\nhttps://ghostscan.xyz NXDOMAIN.\n", ["Fonte indisponível", "Mangá"], created_at="2026-11-03T10:00:00Z"),
            True, True,
        ),
        (
            "ds_neg_temporary_outage", "dead_source",
            _mk(4003, "[Fonte indisponível]: GhostScan", "### Tipo de extensão\nMangá\n### Nome da extensão\nGhostScan\n### O que aconteceu?\nO site está em manutenção temporária desde hoje, erro 503.\n", ["Fonte indisponível", "Mangá"], created_at="2026-11-01T10:00:00Z"),
            _mk(4004, "[Fonte indisponível]: GhostScan", "### Tipo de extensão\nMangá\n### Nome da extensão\nGhostScan\n### O que aconteceu?\nO domínio expirou há meses e confirmaram encerramento definitivo.\n", ["Fonte indisponível", "Mangá"], created_at="2026-11-03T10:00:00Z"),
            False, True,
        ),
        (
            "ds_neg_distant_dates", "dead_source",
            _mk(4005, "[Fonte indisponível]: OldSite", "### Tipo de extensão\nAnime\n### Nome da extensão\nOldSite\n### O que aconteceu?\nDomínio expirou e equipe confirmou encerramento definitivo.\n", ["Fonte indisponível", "Anime"], created_at="2026-08-01T10:00:00Z"),
            _mk(4006, "[Fonte indisponível]: OldSite", "### Tipo de extensão\nAnime\n### Nome da extensão\nOldSite\n### O que aconteceu?\nDomínio expirou e equipe confirmou encerramento definitivo.\n", ["Fonte indisponível", "Anime"], created_at="2026-11-15T10:00:00Z"),
            False, False,
        ),
        (
            "ds_neg_different_sources", "dead_source",
            _mk(4007, "[Fonte indisponível]: FonteAlfa", "### Tipo de extensão\nMangá\n### Nome da extensão\nFonteAlfa\n### O que aconteceu?\nSite encerrou definitivamente.\n", ["Fonte indisponível", "Mangá"]),
            _mk(4008, "[Fonte indisponível]: FonteBeta", "### Tipo de extensão\nMangá\n### Nome da extensão\nFonteBeta\n### O que aconteceu?\nSite encerrou definitivamente.\n", ["Fonte indisponível", "Mangá"], created_at="2026-11-02T10:00:00Z"),
            False, False,
        ),

        # --- ENHANCEMENT ---
        (
            "en_pos_exact_same_feature", "enhancement",
            _mk(5001, "[Sugestão]: Filtro por ano de lançamento", "### Título ou resumo\nAdicionar filtro por ano de lançamento no catálogo\n### Descrição da melhoria\nPermitir filtrar obras por ano de lançamento no catálogo.\n### Extensão relacionada (opcional)\nSukiMangas\n", ["Sugestão"]),
            _mk(5002, "[Sugestão]: Filtro por ano de lançamento", "### Título ou resumo\nAdicionar filtro por ano de lançamento no catálogo\n### Descrição da melhoria\nPermitir filtrar obras por ano de lançamento no catálogo.\n### Extensão relacionada (opcional)\nSukiMangas\n", ["Sugestão"], created_at="2026-11-02T10:00:00Z"),
            True, True,
        ),
        (
            "en_neg_real_144_151_diff_extensions", "enhancement",
            real[144], real[151], False, False,
        ),
        (
            "en_neg_similar_global_complementary", "enhancement",
            _mk(5003, "[Sugestão]: Exportar histórico de leitura em JSON", "### Título ou resumo\nExportar histórico de leitura em JSON\n### Descrição da melhoria\nPermitir exportar manualmente um backup do histórico de leitura para arquivo JSON local.\n", ["Sugestão"]),
            _mk(5004, "[Sugestão]: Sincronizar progresso com AniList", "### Título ou resumo\nSincronizar progresso com AniList\n### Descrição da melhoria\nIntegrar a lista e o histórico de capítulos lidos automaticamente via OAuth com o AniList.\n", ["Sugestão"], created_at="2026-11-02T10:00:00Z"),
            False, False,
        ),
        (
            "en_neg_paraphrase_human_review", "enhancement",
            _mk(5005, "[Sugestão]: Filtro por gênero e ano na busca", "### Título ou resumo\nFiltro por gênero e ano na busca avançada\n### Descrição da melhoria\nAdicionar seletores para filtrar por múltiplos gêneros e intervalo de ano na busca da extensão.\n### Extensão relacionada (opcional)\nKuroMangas\n", ["Sugestão"]),
            _mk(5006, "[Sugestão]: Melhorar filtros da busca com gêneros", "### Título ou resumo\nMelhorar filtros da busca com seleção de gêneros\n### Descrição da melhoria\nIncluir opção de filtrar por gênero e ano dentro da barra de busca avançada da extensão.\n### Extensão relacionada (opcional)\nKuroMangas\n", ["Sugestão"], created_at="2026-11-02T10:00:00Z"),
            False, True,
        ),
        # Additional negative cross-pairs on real open issues to expand benchmark >= 40 pairs
        ("real_cross_156_128", "bug", real[128], real[156], False, False),
        ("real_cross_145_146", "bug", real[145], real[146], False, False),
        ("real_cross_146_148", "bug", real[146], real[148], False, False),
        ("real_cross_125_134", "bug", real[125], real[134], False, False),
        ("real_cross_137_138", "domain_change", real[137], real[138], False, False),
        ("real_cross_123_126", "new_extension", real[123], real[126], False, False),
        ("real_cross_126_140", "new_extension", real[126], real[140], False, False),
        ("real_cross_140_143", "new_extension", real[140], real[143], False, False),
        # Semantic & regression protection cases
        ("real_semantic_141_143_closed", "new_extension", real[141], real[143], False, False),
        (
            "real_semantic_141_143_simulated_open", "new_extension",
            _mk(141, real[141].title, real[141].body, ["Nova extensão", "Anime", "Conteúdo +18"], state="open", created_at=real[141].created_at),
            real[143], False, False,
        ),
        ("real_reg_63_111_little_tyrant", "bug", real[63], real[111], False, False),
        ("real_reg_46_152_cerise_scan", "bug", real[46], real[152], False, False),
        (
            "semantic_same_site_diff_lang_en_pt", "new_extension",
            _mk(6001, "[Nova extensão]: DualExt", "### Tipo de extensão\nMangá\n### Nome da fonte\nDualExt\n### URL do site\nhttps://dualext.com/en/\n### Idioma\nInglês\n", ["Nova extensão", "Mangá"]),
            _mk(6002, "[Nova extensão]: DualExt", "### Tipo de extensão\nMangá\n### Nome da fonte\nDualExt\n### URL do site\nhttps://dualext.com/pt/\n### Idioma\nPortuguês (Brasil)\n", ["Nova extensão", "Mangá"], created_at="2026-11-02T10:00:00Z"),
            False, False,
        ),
        (
            "semantic_same_site_diff_features", "enhancement",
            _mk(6003, "[Sugestão]: Suki — Login", "### Título ou resumo\nSuporte a login na Suki\n### Descrição da melhoria\nImplementar cadastro e login de usuário.\n### Extensão relacionada (opcional)\nSuki\n", ["Sugestão"]),
            _mk(6004, "[Sugestão]: Suki — Filtro por gênero", "### Título ou resumo\nFiltro por gênero na Suki\n### Descrição da melhoria\nAdicionar filtro por gênero e ano na busca.\n### Extensão relacionada (opcional)\nSuki\n", ["Sugestão"], created_at="2026-11-02T10:00:00Z"),
            False, False,
        ),
        (
            "bug_diff_symptom_wrong_chapters_vs_no_pages", "bug",
            _mk(6005, "[Problema na extensão]: FonteY — Capítulos errados", "### Tipo de extensão\nMangá\n### Nome da extensão\nFonteY\n### Parte afetada\nCapítulos\n### O que está acontecendo?\nCapítulos trocados de outra obra e fora de ordem.\n", ["Problema na extensão", "Mangá"]),
            _mk(6006, "[Problema na extensão]: FonteY — Nenhuma página", "### Tipo de extensão\nMangá\n### Nome da extensão\nFonteY\n### Parte afetada\nCapítulos\n### O que está acontecendo?\nErro No pages found ao abrir capítulo.\n", ["Problema na extensão", "Mangá"], created_at="2026-11-02T10:00:00Z"),
            False, False,
        ),
    ]
    return cases


def compute_metrics():
    cfg = load_config(REPO_ROOT / ".github" / "duplicate-guard.json")
    cases = build_labeled_cases()

    tp_close = fp_close = fn_close = tn_close = 0
    tp_alert = fp_alert = fn_alert = tn_alert = 0
    per_cat = defaultdict(lambda: {"total": 0, "close_tp": 0, "close_fp": 0, "close_fn": 0, "alert_tp": 0, "alert_fp": 0, "alert_fn": 0})
    failures: list[str] = []

    for case_id, cat, older, newer, exp_close, exp_alert in cases:
        parsed = parse_corpus([older, newer])
        matcher = DuplicateMatcher(parsed, cfg)
        dec = matcher.evaluate(next(p for p in parsed if p.raw.number == newer.number))

        pred_close = dec.classification == "confirmed_duplicate" and dec.should_auto_close
        pred_alert = dec.classification in ("confirmed_duplicate", "possible_duplicate")

        per_cat[cat]["total"] += 1
        if pred_close and exp_close:
            tp_close += 1
            per_cat[cat]["close_tp"] += 1
        elif pred_close and not exp_close:
            fp_close += 1
            per_cat[cat]["close_fp"] += 1
            failures.append(f"FP auto-close on {case_id}: {dec.classification} -> #{dec.canonical_issue} ({dec.reasons})")
        elif not pred_close and exp_close:
            fn_close += 1
            per_cat[cat]["close_fn"] += 1
            failures.append(f"FN auto-close on {case_id}: {dec.classification} ({dec.reasons})")
        else:
            tn_close += 1

        if pred_alert and exp_alert:
            tp_alert += 1
            per_cat[cat]["alert_tp"] += 1
        elif pred_alert and not exp_alert:
            fp_alert += 1
            per_cat[cat]["alert_fp"] += 1
            failures.append(f"FP alert on {case_id}: {dec.classification} -> #{dec.canonical_issue}")
        elif not pred_alert and exp_alert:
            fn_alert += 1
            per_cat[cat]["alert_fn"] += 1
            failures.append(f"FN alert on {case_id}: {dec.classification}")
        else:
            tn_alert += 1

    close_prec = tp_close / (tp_close + fp_close) if (tp_close + fp_close) else 1.0
    close_rec = tp_close / (tp_close + fn_close) if (tp_close + fn_close) else 1.0
    alert_prec = tp_alert / (tp_alert + fp_alert) if (tp_alert + fp_alert) else 1.0
    alert_rec = tp_alert / (tp_alert + fn_alert) if (tp_alert + fn_alert) else 1.0

    return {
        "total_cases": len(cases),
        "auto_close": {"tp": tp_close, "fp": fp_close, "fn": fn_close, "tn": tn_close, "precision": close_prec, "recall": close_rec},
        "alert_or_close": {"tp": tp_alert, "fp": fp_alert, "fn": fn_alert, "tn": tn_alert, "precision": alert_prec, "recall": alert_rec},
        "per_category": dict(per_cat),
        "failures": failures,
    }


def test_duplicate_guard_quality_metrics():
    summary = compute_metrics()
    print("\n=== NOX DUPLICATE GUARD BENCHMARK METRICS ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    assert summary["total_cases"] >= 40
    assert summary["auto_close"]["fp"] == 0, f"Auto-close false positives found: {summary['failures']}"
    assert summary["auto_close"]["precision"] == 1.0
    assert summary["auto_close"]["recall"] >= 0.90
    assert summary["alert_or_close"]["fp"] == 0, f"Unexpected alert FP: {summary['failures']}"
    assert summary["alert_or_close"]["recall"] >= 0.90
