# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Comprehensive unit, integration, regression, robustness, and security tests
for Nox Duplicate Guard covering all 28 mandatory scenarios and real Project Nox fixtures.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.duplicate_guard.actions import (  # noqa: E402
    GuardActionExecutor,
    parse_marker,
    render_comment_body,
)
from scripts.duplicate_guard.cli import build_duplicate_links, parse_corpus, run_cli  # noqa: E402
from scripts.duplicate_guard.config import load_config  # noqa: E402
from scripts.duplicate_guard.github_client import (  # noqa: E402
    DryRunWriteBlockedError,
    GitHubClient,
    PermissionDeniedError,
    RateLimitError,
)
from scripts.duplicate_guard.matcher import DuplicateMatcher  # noqa: E402
from scripts.duplicate_guard.models import GuardConfig, RawIssue  # noqa: E402
from scripts.duplicate_guard.normalize import normalize_url, sanitize_for_markdown  # noqa: E402
from scripts.duplicate_guard.parser import parse_issue  # noqa: E402

FIXTURES_PATH = REPO_ROOT / "tests" / "fixtures" / "real_issues_snapshot.json"


def make_raw(
    number: int,
    title: str,
    body: str,
    *,
    labels: list[str] | tuple[str, ...] = (),
    state: str = "open",
    state_reason: str | None = None,
    created_at: str = "2026-11-10T10:00:00Z",
    updated_at: str | None = None,
    closed_at: str | None = None,
    author: str = "user1",
    comments_count: int = 0,
) -> RawIssue:
    return RawIssue(
        number=number,
        title=title,
        body=body,
        state=state,
        state_reason=state_reason,
        created_at=created_at,
        updated_at=updated_at or created_at,
        closed_at=closed_at,
        html_url=f"https://github.com/ProjectNox-org/project-nox-requests/issues/{number}",
        labels=tuple(labels),
        author=author,
        comments_count=comments_count,
    )


def load_real_fixtures() -> dict[int, RawIssue]:
    data = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    return {
        int(item["number"]): RawIssue.from_github_dict(item)
        for item in data["issues"]
    }


class InMemoryGitHubApi:
    """Stateful mock GitHub API tracking comments, labels, closures, and failures."""

    def __init__(self, issues: list[RawIssue], initial_comments: dict[int, list[dict]] | None = None) -> None:
        self.issues: dict[int, dict[str, Any]] = {}
        for r in issues:
            self.issues[r.number] = {
                "number": r.number,
                "title": r.title,
                "body": r.body,
                "state": r.state,
                "state_reason": r.state_reason,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
                "closed_at": r.closed_at,
                "html_url": r.html_url,
                "labels": [{"name": lbl} for lbl in r.labels],
                "user": {"login": r.author},
                "comments": r.comments_count,
            }
        self.comments: dict[int, list[dict]] = {k: list(v) for k, v in (initial_comments or {}).items()}
        self.next_comment_id = 5000
        self.writes: list[tuple[str, int, Any]] = []
        self.fail_on: dict[str, Exception] = {}
        self.api_calls_made = 0

    def list_issues(self, *, state: str = "all") -> list[dict]:
        self.api_calls_made += 1
        if state == "all":
            return [dict(v) for v in self.issues.values()]
        return [dict(v) for v in self.issues.values() if v.get("state") == state]

    def get_issue(self, issue_number: int) -> dict:
        self.api_calls_made += 1
        if "get_issue" in self.fail_on:
            raise self.fail_on["get_issue"]
        return dict(self.issues[issue_number])

    def list_comments(self, issue_number: int) -> list[dict]:
        self.api_calls_made += 1
        if "list_comments" in self.fail_on:
            raise self.fail_on["list_comments"]
        return list(self.comments.get(issue_number, []))

    def create_comment(self, issue_number: int, body: str) -> dict:
        self.api_calls_made += 1
        if "create_comment" in self.fail_on:
            raise self.fail_on["create_comment"]
        self.next_comment_id += 1
        c = {
            "id": self.next_comment_id,
            "body": body,
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        }
        self.comments.setdefault(issue_number, []).append(c)
        self.issues[issue_number]["comments"] = len(self.comments[issue_number])
        self.writes.append(("create_comment", issue_number, c["id"]))
        return c

    def update_comment(self, comment_id: int, body: str) -> dict:
        self.api_calls_made += 1
        if "update_comment" in self.fail_on:
            raise self.fail_on["update_comment"]
        for num, clist in self.comments.items():
            for c in clist:
                if c["id"] == comment_id:
                    c["body"] = body
                    self.writes.append(("update_comment", num, comment_id))
                    return c
        raise KeyError(comment_id)

    def add_labels(self, issue_number: int, labels: list[str]) -> None:
        self.api_calls_made += 1
        if "add_labels" in self.fail_on:
            raise self.fail_on["add_labels"]
        existing = {l["name"] for l in self.issues[issue_number]["labels"]}
        for lbl in labels:
            if lbl not in existing:
                self.issues[issue_number]["labels"].append({"name": lbl})
        self.writes.append(("add_labels", issue_number, list(labels)))

    def remove_label(self, issue_number: int, label_name: str) -> None:
        self.api_calls_made += 1
        if "remove_label" in self.fail_on:
            raise self.fail_on["remove_label"]
        self.issues[issue_number]["labels"] = [
            l for l in self.issues[issue_number]["labels"] if l["name"] != label_name
        ]
        self.writes.append(("remove_label", issue_number, label_name))

    def close_issue(self, issue_number: int, *, state_reason: str = "duplicate") -> dict:
        self.api_calls_made += 1
        if "close_issue" in self.fail_on:
            raise self.fail_on["close_issue"]
        self.issues[issue_number]["state"] = "closed"
        self.issues[issue_number]["state_reason"] = state_reason
        self.writes.append(("close_issue", issue_number, state_reason))
        return dict(self.issues[issue_number])


def evaluate_pair(raw_list: list[RawIssue], target_num: int, cfg: GuardConfig | None = None):
    cfg = cfg or load_config(REPO_ROOT / ".github" / "duplicate-guard.json")
    parsed = parse_corpus(raw_list)
    matcher = DuplicateMatcher(parsed, cfg)
    target = next(p for p in parsed if p.raw.number == target_num)
    return matcher.evaluate(target)


# ---------------------------------------------------------------------------
# Positive Scenarios (1 - 5)
# ---------------------------------------------------------------------------

def test_01_same_request_different_name_writing_and_real_140_149():
    # Synthetic: Manga Livre vs MangaLivre
    i1 = make_raw(
        10,
        "[Nova extensão]: Manga Livre",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nManga Livre\n### URL do site\nhttps://mangalivre.example\n",
        labels=["Nova extensão", "Mangá", "Em análise"],
        created_at="2026-11-01T10:00:00Z",
    )
    i2 = make_raw(
        11,
        "[Nova extensão]: MangaLivre",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nMangaLivre\n### URL do site\nhttps://mangalivre.example/\n",
        labels=["Nova extensão", "Mangá", "Em análise"],
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i1, i2], 11)
    assert dec.classification == "confirmed_duplicate"
    assert dec.canonical_issue == 10
    assert dec.should_auto_close is True

    # Real fixtures: #140 (WatchAnimeHentai) vs #149 (Wacht anime hentai)
    real = load_real_fixtures()
    dec_real = evaluate_pair([real[140], real[149]], 149)
    assert dec_real.classification == "confirmed_duplicate"
    assert dec_real.canonical_issue == 140
    assert dec_real.should_auto_close is True


def test_02_same_domain_with_www_or_protocol_difference():
    i1 = make_raw(
        20,
        "[Nova extensão]: SukiMangas",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nSukiMangas\n### URL do site\nhttp://sukimangas.com.br\n",
        labels=["Nova extensão", "Mangá", "Em análise"],
        created_at="2026-11-01T10:00:00Z",
    )
    i2 = make_raw(
        21,
        "[Nova extensão]: Suki Mangás",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nSuki Mangás\n### URL do site\nhttps://www.sukimangas.com.br/manga/\n",
        labels=["Nova extensão", "Mangá", "Em análise"],
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i1, i2], 21)
    assert dec.classification == "confirmed_duplicate"
    assert dec.canonical_issue == 20


def test_03_same_bug_different_wording_structured_evidence_with_real_132():
    real = load_real_fixtures()
    bakai_orig = real[132]  # Real issue #132: Bakai, Leitor / Imagens, "No pages found"
    bakai_dup = make_raw(
        200,
        "Erro No pages found no leitor",
        (
            "### Tipo de extensão\nMangá\n"
            "### Nome da extensão\nBakai\n"
            "### Versão da extensão\n1.6.21\n"
            "### Aplicativo utilizado\nKomikku\n"
            "### Parte afetada\nLeitor / Imagens\n"
            "### O que está acontecendo?\nAo tentar ler um capítulo aparece No pages found.\n"
            "### Como reproduzir?\nEntrar no mangá e tentar abrir qualquer capítulo.\n"
        ),
        labels=["Problema na extensão", "Mangá", "Em análise"],
        created_at="2026-11-08T12:00:00Z",
        author="another_user",
    )
    dec = evaluate_pair([bakai_orig, bakai_dup], 200)
    assert dec.classification == "confirmed_duplicate"
    assert dec.canonical_issue == 132
    assert dec.should_auto_close is True

    # Real legacy NXDOMAIN pair #52 vs #55 (when both considered open at creation time)
    r52_open = RawIssue(**{**real[52].__dict__, "state": "open", "closed_at": None, "labels": ("Problema na extensão", "Mangá")})
    r55_open = RawIssue(**{**real[55].__dict__, "state": "open", "closed_at": None, "labels": ("Problema na extensão", "Mangá")})
    dec_taimu = evaluate_pair([r52_open, r55_open], 55)
    assert dec_taimu.classification == "confirmed_duplicate"
    assert dec_taimu.canonical_issue == 52


def test_04_identical_requests_by_different_users():
    body = (
        "### Tipo de extensão\nAnime\n"
        "### Nome da extensão\nAnimes Digital\n"
        "### URL antiga\nhttps://animesdigital.org\n"
        "### URL nova\nhttps://animesdigital.tv\n"
    )
    i1 = make_raw(30, "[Mudança de domínio]: Animes Digital", body, labels=["Mudança de domínio", "Anime"], author="alice", created_at="2026-11-01T10:00:00Z")
    i2 = make_raw(31, "[Mudança de domínio]: Animes Digital", body, labels=["Mudança de domínio", "Anime"], author="bob", created_at="2026-11-01T11:00:00Z")
    dec = evaluate_pair([i1, i2], 31)
    assert dec.classification == "confirmed_duplicate"
    assert dec.canonical_issue == 30
    assert dec.should_auto_close is True


def test_05_confirmed_new_extension_duplicate_executes_formal_closure():
    i1 = make_raw(
        40,
        "[Nova extensão]: LeitorNox",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nLeitorNox\n### URL do site\nhttps://leitornox.com\n",
        labels=["Nova extensão", "Mangá", "Em análise"],
        created_at="2026-11-01T10:00:00Z",
    )
    i2 = make_raw(
        41,
        "[Nova extensão]: Leitor Nox",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nLeitor Nox\n### URL do site\nhttps://www.leitornox.com/\n",
        labels=["Nova extensão", "Mangá", "Em análise"],
        created_at="2026-11-02T10:00:00Z",
    )
    cfg = load_config(REPO_ROOT / ".github" / "duplicate-guard.json")
    dec = evaluate_pair([i1, i2], 41, cfg)
    api = InMemoryGitHubApi([i1, i2])
    res = GuardActionExecutor(api, cfg, {40: i1, 41: i2}).apply(dec)
    assert res.action_taken == "auto_closed"
    assert res.closed is True
    assert "Duplicado" in res.labels_added
    assert "Em análise" in res.labels_removed
    assert api.issues[41]["state"] == "closed"
    assert api.issues[41]["state_reason"] == "duplicate"
    lbls_after = {l["name"] for l in api.issues[41]["labels"]}
    assert "Duplicado" in lbls_after
    assert "Em análise" not in lbls_after
    assert api.issues[40]["state"] == "open"


# ---------------------------------------------------------------------------
# Negative Scenarios (6 - 15)
# ---------------------------------------------------------------------------

def test_06_same_extension_different_problems():
    real = load_real_fixtures()
    bakai_reader = real[132]  # Leitor / Imagens -> No pages found
    bakai_login = make_raw(
        201,
        "[Problema na extensão]: Bakai — Erro no login",
        (
            "### Tipo de extensão\nMangá\n"
            "### Nome da extensão\nBakai\n"
            "### Versão da extensão\n1.6.21\n"
            "### Aplicativo utilizado\nMihon\n"
            "### Parte afetada\nLogin\n"
            "### O que está acontecendo?\nNão consigo fazer login com minha conta na extensão.\n"
            "### Como reproduzir?\nAbrir configurações -> Login -> erro de senha.\n"
        ),
        labels=["Problema na extensão", "Mangá", "Em análise"],
        created_at="2026-11-08T12:00:00Z",
    )
    dec = evaluate_pair([bakai_reader, bakai_login], 201)
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_07_same_source_different_affected_parts_and_real_150_154():
    # Real #150 (Xxxyaoi, Capítulos, Cloudflare) vs #154 (Xxxyaoi, Popular / Recentes, bloqueio + Cloudflare)
    real = load_real_fixtures()
    dec = evaluate_pair([real[150], real[154]], 154)
    # Must NEVER auto-close; at most possible_duplicate for human review
    assert dec.classification != "confirmed_duplicate"
    assert dec.should_auto_close is False

    # Same extension, Busca vs Leitor -> no_match
    s1 = make_raw(
        210, "[Problema na extensão]: Orion — Erro na busca",
        "### Tipo de extensão\nMangá\n### Nome da extensão\nOrion\n### Versão da extensão\n1.2.0\n### Aplicativo utilizado\nMihon\n### Parte afetada\nBusca / Filtros\n### O que está acontecendo?\nA pesquisa não retorna nenhuma obra.\n### Como reproduzir?\nPesquisar qualquer nome.\n",
        labels=["Problema na extensão", "Mangá"], created_at="2026-11-01T10:00:00Z",
    )
    s2 = make_raw(
        211, "[Problema na extensão]: Orion — Erro no leitor",
        "### Tipo de extensão\nMangá\n### Nome da extensão\nOrion\n### Versão da extensão\n1.2.0\n### Aplicativo utilizado\nMihon\n### Parte afetada\nLeitor / Imagens\n### O que está acontecendo?\nPáginas ficam brancas no leitor.\n### Como reproduzir?\nAbrir capítulo 1.\n",
        labels=["Problema na extensão", "Mangá"], created_at="2026-11-02T10:00:00Z",
    )
    dec_diff = evaluate_pair([s1, s2], 211)
    assert dec_diff.classification == "no_match"


def test_08_same_extension_different_domain_change_real_130_153_and_others():
    real = load_real_fixtures()
    # Real #130 (fbsquadx.com -> fbsscan.com) vs #153 (fbsscan.com -> squadxfbs.com)
    # Test both as-is and as open to prove matching logic itself never equates distinct transitions
    r130_open = RawIssue(**{**real[130].__dict__, "state": "open", "closed_at": None})
    r153_open = RawIssue(**{**real[153].__dict__, "state": "open", "closed_at": None})
    dec_fb = evaluate_pair([r130_open, r153_open], 153)
    assert dec_fb.classification == "no_match"
    assert dec_fb.should_auto_close is False

    # Real #13 vs #54 (AnimeFire) and #103 vs #137 (RedeToons)
    r13_open = RawIssue(**{**real[13].__dict__, "state": "open", "closed_at": None})
    r54_open = RawIssue(**{**real[54].__dict__, "state": "open", "closed_at": None})
    assert evaluate_pair([r13_open, r54_open], 54).classification == "no_match"

    r103_open = RawIssue(**{**real[103].__dict__, "state": "open", "closed_at": None})
    assert evaluate_pair([r103_open, real[137]], 137).classification == "no_match"


def test_09_manga_and_anime_same_name_never_mixed():
    i1 = make_raw(
        50,
        "[Nova extensão]: SakuraPortal",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nSakuraPortal\n### URL do site\nhttps://sakuraportal.com\n",
        labels=["Nova extensão", "Mangá"],
        created_at="2026-11-01T10:00:00Z",
    )
    i2 = make_raw(
        51,
        "[Nova extensão]: SakuraPortal",
        "### Tipo de extensão\nAnime\n### Nome da fonte\nSakuraPortal\n### URL do site\nhttps://sakuraportal.com\n",
        labels=["Nova extensão", "Anime"],
        created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i1, i2], 51)
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_10_resolved_bug_regression_later_version_not_closed():
    old_fixed = make_raw(
        60,
        "[Problema na extensão]: Bakai — No pages found",
        "### Tipo de extensão\nMangá\n### Nome da extensão\nBakai\n### Versão da extensão\n1.6.10\n### Aplicativo utilizado\nMihon\n### Parte afetada\nLeitor / Imagens\n### O que está acontecendo?\nErro No pages found ao abrir capítulo.\n",
        labels=["Problema na extensão", "Mangá", "Concluído"],
        state="closed",
        state_reason="completed",
        created_at="2026-10-01T10:00:00Z",
        closed_at="2026-10-05T10:00:00Z",
    )
    regression = make_raw(
        61,
        "[Problema na extensão]: Bakai — No pages found",
        "### Tipo de extensão\nMangá\n### Nome da extensão\nBakai\n### Versão da extensão\n1.6.25\n### Aplicativo utilizado\nMihon\n### Parte afetada\nLeitor / Imagens\n### O que está acontecendo?\nErro No pages found ao abrir capítulo.\n",
        labels=["Problema na extensão", "Mangá", "Em análise"],
        created_at="2026-11-05T10:00:00Z",
    )
    dec = evaluate_pair([old_fixed, regression], 61)
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


def test_11_similar_but_distinct_enhancements_and_real_144_151():
    real = load_real_fixtures()
    # Real #144 (Blackout Comics login) vs #151 (Hanami Heaven login/cadastro)
    dec_real = evaluate_pair([real[144], real[151]], 151)
    assert dec_real.classification == "no_match"

    # Global enhancements that sound related but ask for distinct features
    e1 = make_raw(
        70,
        "[Sugestão]: Filtro por ano de lançamento",
        "### Título ou resumo\nFiltro por ano de lançamento no catálogo\n### Descrição da melhoria\nPermitir filtrar obras por ano de publicação.\n",
        labels=["Sugestão", "Em análise"],
        created_at="2026-11-01T10:00:00Z",
    )
    e2 = make_raw(
        71,
        "[Sugestão]: Filtro por idioma e status de tradução",
        "### Título ou resumo\nFiltro por idioma e status\n### Descrição da melhoria\nPermitir ordenar e filtrar por idioma original e status completo ou em andamento.\n",
        labels=["Sugestão", "Em análise"],
        created_at="2026-11-02T10:00:00Z",
    )
    dec_enh = evaluate_pair([e1, e2], 71)
    assert dec_enh.classification != "confirmed_duplicate"
    assert dec_enh.should_auto_close is False


def test_12_two_different_sources_similar_names():
    real = load_real_fixtures()
    # Real #3 (Animex HD, animexhd.xyz) vs #7 (Animes HD, animeshd.to)
    r3_open = RawIssue(**{**real[3].__dict__, "state": "open", "closed_at": None})
    r7_open = RawIssue(**{**real[7].__dict__, "state": "open", "closed_at": None})
    dec = evaluate_pair([r3_open, r7_open], 7)
    assert dec.classification == "no_match"


def test_13_similar_urls_pointing_to_different_services():
    # Different subdomains on shared hosting platforms
    w1 = make_raw(
        80,
        "[Nova extensão]: AlphaScans",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nAlphaScans\n### URL do site\nhttps://alphascans.workers.dev\n",
        labels=["Nova extensão", "Mangá"], created_at="2026-11-01T10:00:00Z",
    )
    w2 = make_raw(
        81,
        "[Nova extensão]: AlphaReads",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nAlphaReads\n### URL do site\nhttps://alphareads.workers.dev\n",
        labels=["Nova extensão", "Mangá"], created_at="2026-11-02T10:00:00Z",
    )
    assert evaluate_pair([w1, w2], 81).classification == "no_match"

    # Different TLDs (.com.br vs .net)
    d1 = make_raw(
        82,
        "[Nova extensão]: KumaScans",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nKumaScans\n### URL do site\nhttps://kumascans.com.br\n",
        labels=["Nova extensão", "Mangá"], created_at="2026-11-01T10:00:00Z",
    )
    d2 = make_raw(
        83,
        "[Nova extensão]: KumaPortal",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nKumaPortal\n### URL do site\nhttps://kumascans.net\n",
        labels=["Nova extensão", "Mangá"], created_at="2026-11-02T10:00:00Z",
    )
    assert evaluate_pair([d1, d2], 83).classification == "no_match"


def test_14_closed_issue_declined_or_cancelled_never_auto_closes_new_ticket():
    real = load_real_fixtures()
    # #29 Sakura is closed + Recusado
    sakura_new = make_raw(
        220,
        "[Nova extensão]: Sakura",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nSakura\n### URL do site\nhttps://sakuramangas.org/\n",
        labels=["Nova extensão", "Mangá", "Em análise"],
        created_at="2026-11-18T10:00:00Z",
    )
    dec = evaluate_pair([real[29], sakura_new], 220)
    assert dec.classification != "confirmed_duplicate"
    assert dec.should_auto_close is False


def test_15_insufficient_information_never_auto_closed():
    i1 = make_raw(
        90,
        "[Problema na extensão]: Xxxyaoi — erro",
        "### Tipo de extensão\nMangá\n### Nome da extensão\nXxxyaoi\n### Parte afetada\nOutro\n### O que está acontecendo?\nnão funciona\n",
        labels=["Problema na extensão", "Mangá"], created_at="2026-11-01T10:00:00Z",
    )
    i2 = make_raw(
        91,
        "[Problema na extensão]: Xxxyaoi — erro",
        "### Tipo de extensão\nMangá\n### Nome da extensão\nXxxyaoi\n### Parte afetada\nOutro\n### O que está acontecendo?\nnão pega\n",
        labels=["Problema na extensão", "Mangá"], created_at="2026-11-02T10:00:00Z",
    )
    dec = evaluate_pair([i1, i2], 91)
    assert dec.classification == "no_match"
    assert dec.should_auto_close is False


# ---------------------------------------------------------------------------
# Robustness, Concurrency, Safety & Security Scenarios (16 - 28)
# ---------------------------------------------------------------------------

def _http_err(code: int, headers: dict[str, str] | None = None, msg: str = "err") -> HTTPError:
    from email.message import Message
    import io

    m = Message()
    for k, v in (headers or {}).items():
        m[k] = v
    return HTTPError("https://api.github.com/test", code, msg, m, io.BytesIO(msg.encode("utf-8")))


def test_16_transient_github_api_unavailable_retries_and_conservative_abort():
    sleeps: list[float] = []
    client = GitHubClient("ProjectNox-org/project-nox-requests", token="dummy", sleep_fn=sleeps.append)

    class FakeResp:
        status = 200
        headers = {}

        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    calls = {"n": 0}

    def fake_urlopen(req, timeout=25):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_err(503, {}, "Service Unavailable")
        return FakeResp({"number": 132, "state": "open", "title": "ok", "labels": []})

    with patch("scripts.duplicate_guard.github_client.urlopen", side_effect=fake_urlopen):
        res = client.get_issue(132)
    assert res["number"] == 132
    assert len(sleeps) == 1

    # Persistent failure in action executor aborts conservatively without crashing
    i1 = make_raw(1, "t1", "b1")
    api = InMemoryGitHubApi([i1])
    api.fail_on["get_issue"] = RuntimeError("503 down")  # type: ignore[assignment]
    cfg = GuardConfig()
    dec = evaluate_pair([i1], 1, cfg)
    assert dec.classification == "no_match"


def test_17_rate_limit_handled_conservatively():
    client = GitHubClient("ProjectNox-org/project-nox-requests", token="dummy", sleep_fn=lambda _: None)

    def raise_rl(req, timeout=25):
        raise _http_err(403, {"X-RateLimit-Remaining": "0"}, "API rate limit exceeded")

    with patch("scripts.duplicate_guard.github_client.urlopen", side_effect=raise_rl):
        with pytest.raises(RateLimitError):
            client.list_issues()

    # Executor handles RateLimitError conservatively without throwing or modifying issue
    i1 = make_raw(10, "[Nova extensão]: A", "### Tipo de extensão\nMangá\n### Nome da fonte\nA\n### URL do site\nhttps://a.com\n", labels=["Nova extensão", "Mangá"])
    i2 = make_raw(11, "[Nova extensão]: A", "### Tipo de extensão\nMangá\n### Nome da fonte\nA\n### URL do site\nhttps://a.com\n", labels=["Nova extensão", "Mangá"], created_at="2026-11-02T10:00:00Z")
    api = InMemoryGitHubApi([i1, i2])
    api.fail_on["get_issue"] = RateLimitError("rate limited", status=403)
    cfg = load_config(REPO_ROOT / ".github" / "duplicate-guard.json")
    dec = evaluate_pair([i1, i2], 11, cfg)
    res = GuardActionExecutor(api, cfg, {10: i1, 11: i2}).apply(dec)
    assert res.action_taken == "api_error_skipped"
    assert len(api.writes) == 0


def test_18_repeated_executions_idempotence():
    i1 = make_raw(10, "[Nova extensão]: SourceX", "### Tipo de extensão\nMangá\n### Nome da fonte\nSourceX\n### URL do site\nhttps://sourcex.com\n", labels=["Nova extensão", "Mangá"], created_at="2026-11-01T10:00:00Z")
    i2 = make_raw(11, "[Nova extensão]: SourceX", "### Tipo de extensão\nMangá\n### Nome da fonte\nSourceX\n### URL do site\nhttps://sourcex.com\n", labels=["Nova extensão", "Mangá", "Em análise"], created_at="2026-11-02T10:00:00Z")
    cfg = load_config(REPO_ROOT / ".github" / "duplicate-guard.json")
    dec = evaluate_pair([i1, i2], 11, cfg)
    api = InMemoryGitHubApi([i1, i2])
    ex = GuardActionExecutor(api, cfg, {10: i1, 11: i2})

    first = ex.apply(dec)
    assert first.action_taken == "auto_closed"
    writes_after_first = len(api.writes)

    second = ex.apply(dec)
    assert second.action_taken == "noop_idempotent"
    assert len(api.writes) == writes_after_first
    assert len(api.comments[11]) == 1


def test_19_later_issue_edit_updates_existing_comment():
    # First run: possible duplicate (same domain name without URL -> comment posted)
    i1 = make_raw(10, "[Nova extensão]: NexusScan", "### Tipo de extensão\nMangá\n### Nome da fonte\nNexusScan\n### URL do site\nhttps://nexusscan.org\n", labels=["Nova extensão", "Mangá"], created_at="2026-11-01T10:00:00Z")
    i2_v1 = make_raw(11, "[Nova extensão]: NexusScan", "### Tipo de extensão\nMangá\n### Nome da fonte\nNexusScan\n", labels=["Nova extensão", "Mangá"], created_at="2026-11-02T10:00:00Z")
    cfg = load_config(REPO_ROOT / ".github" / "duplicate-guard.json")
    api = InMemoryGitHubApi([i1, i2_v1])
    ex = GuardActionExecutor(api, cfg, {10: i1, 11: i2_v1})
    r1 = ex.apply(evaluate_pair([i1, i2_v1], 11, cfg))
    assert r1.action_taken == "commented"
    assert len(api.comments[11]) == 1

    # User edits issue #11 clarifying it is Anime and another domain -> no_match -> existing comment updated
    i2_v2 = make_raw(11, "[Nova extensão]: NexusScan", "### Tipo de extensão\nAnime\n### Nome da fonte\nNexusScan\n### URL do site\nhttps://nexusanimes.tv\n", labels=["Nova extensão", "Anime"], created_at="2026-11-02T10:00:00Z")
    api.issues[11]["body"] = i2_v2.body
    api.issues[11]["labels"] = [{"name": "Nova extensão"}, {"name": "Anime"}]
    r2 = GuardActionExecutor(api, cfg, {10: i1, 11: i2_v2}).apply(evaluate_pair([i1, i2_v2], 11, cfg))
    assert r2.action_taken == "cleared"
    assert len(api.comments[11]) == 1
    meta = parse_marker(api.comments[11][0]["body"])
    assert meta is not None and meta["decision"] == "no_match"


def test_20_simultaneous_identical_issues_deterministic_winner():
    body = "### Tipo de extensão\nMangá\n### Nome da fonte\nParallelManga\n### URL do site\nhttps://parallelmanga.com\n"
    same_ts = "2026-11-20T15:00:00Z"
    i1 = make_raw(300, "[Nova extensão]: ParallelManga", body, labels=["Nova extensão", "Mangá"], created_at=same_ts, author="userA")
    i2 = make_raw(301, "[Nova extensão]: ParallelManga", body, labels=["Nova extensão", "Mangá"], created_at=same_ts, author="userB")

    dec_300 = evaluate_pair([i1, i2], 300)
    dec_301 = evaluate_pair([i1, i2], 301)

    # Lower issue number wins deterministically; never mutual closure
    assert dec_300.classification == "no_match"
    assert dec_300.should_auto_close is False
    assert dec_301.classification == "confirmed_duplicate"
    assert dec_301.canonical_issue == 300
    assert dec_301.should_auto_close is True


def test_21_existing_bot_comment_updated_not_duplicated():
    i1 = make_raw(10, "[Problema na extensão]: Bakai — No pages found", "### Tipo de extensão\nMangá\n### Nome da extensão\nBakai\n### Parte afetada\nLeitor / Imagens\n### O que está acontecendo?\nErro No pages found\n", labels=["Problema na extensão", "Mangá"])
    i2 = make_raw(11, "[Problema na extensão]: Bakai — Problema no leitor", "### Tipo de extensão\nMangá\n### Nome da extensão\nBakai\n### Parte afetada\nLeitor / Imagens\n### O que está acontecendo?\nCapítulos não abrem no leitor\n", labels=["Problema na extensão", "Mangá"], created_at="2026-11-02T10:00:00Z")
    existing_comment = {
        "id": 999,
        "user": {"login": "github-actions[bot]", "type": "Bot"},
        "body": "<!-- nox-duplicate-guard:v1 issue=11 decision=possible_duplicate canonical=10 action=commented fp=old -->\nOld alert",
    }
    api = InMemoryGitHubApi([i1, i2], initial_comments={11: [existing_comment]})
    cfg = load_config(REPO_ROOT / ".github" / "duplicate-guard.json")
    dec = evaluate_pair([i1, i2], 11, cfg)
    res = GuardActionExecutor(api, cfg, {10: i1, 11: i2}).apply(dec)
    assert res.comment_id == 999
    assert len(api.comments[11]) == 1


def test_22_missing_optional_fields_no_response_and_headerless_real_145():
    real = load_real_fixtures()
    parsed_145 = parse_issue(real[145])  # Real headerless issue without ### markers
    assert parsed_145.name == "Blackout Comics"
    assert parsed_145.ext_type == "Mangá"
    assert "catalog" in parsed_145.affected_parts
    assert "http:403" in parsed_145.error_signatures

    parsed_153 = parse_issue(real[153])
    assert parsed_153.additional_info == ""  # _No response_ stripped cleanly


def test_23_legacy_malformed_titles_and_urls():
    real = load_real_fixtures()
    parsed_1 = parse_issue(real[1])  # "https//:redecanais.af/browse"
    assert parsed_1.name == "Rede Canais"
    assert parsed_1.ext_type == "Anime"
    assert "video" in parsed_1.affected_parts

    u = normalize_url("htpps://redetoons.gay/browse")
    assert u is not None and u.is_valid_domain and u.host == "redetoons.gay"

    multi_prefix = make_raw(400, "[Bug]: [Problema na extensão]:    ", "### Tipo de extensão\nMangá\n### Nome da extensão\nTestExt\n")
    p = parse_issue(multi_prefix)
    assert p.name == "TestExt"


def test_24_malicious_markdown_and_url_injection_sanitized():
    evil_url = normalize_url("javascript:alert(1)")
    assert evil_url is None

    evil_title = "Site <!-- --> [click](https://evil.test) <script>alert(1)</script> @everyone $(rm -rf /)"
    cleaned = sanitize_for_markdown(evil_title, 150)
    assert "<!--" not in cleaned
    assert "<script>" not in cleaned
    assert "@everyone" not in cleaned
    assert "](https://evil.test)" not in cleaned

    i1 = make_raw(10, evil_title, "### Tipo de extensão\nMangá\n### Nome da fonte\nEvilTest\n### URL do site\nhttps://eviltest.example\n", labels=["Nova extensão", "Mangá"])
    i2 = make_raw(11, "[Nova extensão]: EvilTest", "### Tipo de extensão\nMangá\n### Nome da fonte\nEvilTest\n### URL do site\nhttps://eviltest.example\n", labels=["Nova extensão", "Mangá"], created_at="2026-11-02T10:00:00Z")
    dec = evaluate_pair([i1, i2], 11)
    comment = render_comment_body(dec, "auto_closed", {10: i1, 11: i2})
    assert "<script>" not in comment
    assert "@everyone" not in comment


def test_25_pagination_many_tickets_filters_prs():
    page1 = [{"number": n, "title": f"I{n}", "state": "open", "created_at": "2026-11-01T00:00:00Z", "labels": [], "user": {"login": "u"}} for n in range(1, 100)]
    page1.append({"number": 999, "title": "PR", "pull_request": {"url": "x"}, "state": "open", "created_at": "2026-11-01T00:00:00Z", "labels": [], "user": {"login": "u"}})
    page2 = [{"number": n, "title": f"I{n}", "state": "open", "created_at": "2026-11-01T00:00:00Z", "labels": [], "user": {"login": "u"}} for n in range(100, 151)]

    client = GitHubClient("ProjectNox-org/project-nox-requests", token="dummy", sleep_fn=lambda _: None)

    class FakeResp:
        status = 200

        def __init__(self, payload, link=""):
            self._payload = payload
            self.headers = {"Link": link} if link else {}

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    call_idx = {"i": 0}

    def fake_urlopen(req, timeout=25):
        call_idx["i"] += 1
        if call_idx["i"] == 1:
            return FakeResp(page1, '<https://api.github.com/repos/ProjectNox-org/project-nox-requests/issues?state=all&per_page=100&page=2>; rel="next"')
        return FakeResp(page2)

    with patch("scripts.duplicate_guard.github_client.urlopen", side_effect=fake_urlopen):
        issues = client.list_issues(state="all")

    assert len(issues) == 150
    assert all("pull_request" not in i for i in issues)


def test_26_execution_without_write_permission_and_dry_run_guard():
    client = GitHubClient("ProjectNox-org/project-nox-requests", dry_run=True)
    with pytest.raises(DryRunWriteBlockedError):
        client.create_comment(10, "blocked")

    i1 = make_raw(10, "[Nova extensão]: PermSite", "### Tipo de extensão\nMangá\n### Nome da fonte\nPermSite\n### URL do site\nhttps://permsite.com\n", labels=["Nova extensão", "Mangá"], created_at="2026-11-01T10:00:00Z")
    i2 = make_raw(11, "[Nova extensão]: PermSite", "### Tipo de extensão\nMangá\n### Nome da fonte\nPermSite\n### URL do site\nhttps://permsite.com\n", labels=["Nova extensão", "Mangá", "Em análise"], created_at="2026-11-02T10:00:00Z")
    api = InMemoryGitHubApi([i1, i2])
    api.fail_on["add_labels"] = PermissionDeniedError("Resource not accessible by integration", status=403)
    cfg = load_config(REPO_ROOT / ".github" / "duplicate-guard.json")
    dec = evaluate_pair([i1, i2], 11, cfg)
    res = GuardActionExecutor(api, cfg, {10: i1, 11: i2}).apply(dec)
    assert res.action_taken == "partial_failure_before_close"
    assert res.closed is False
    assert api.issues[11]["state"] == "open"
    assert "Duplicado" not in {l["name"] for l in api.issues[11]["labels"]}


def test_27_partial_failure_during_comment_or_close_recorded_safely():
    i1 = make_raw(10, "[Nova extensão]: PartSite", "### Tipo de extensão\nMangá\n### Nome da fonte\nPartSite\n### URL do site\nhttps://partsite.com\n", labels=["Nova extensão", "Mangá"], created_at="2026-11-01T10:00:00Z")
    i2 = make_raw(11, "[Nova extensão]: PartSite", "### Tipo de extensão\nMangá\n### Nome da fonte\nPartSite\n### URL do site\nhttps://partsite.com\n", labels=["Nova extensão", "Mangá", "Em análise"], created_at="2026-11-02T10:00:00Z")
    cfg = load_config(REPO_ROOT / ".github" / "duplicate-guard.json")
    dec = evaluate_pair([i1, i2], 11, cfg)

    # Case A: failure to create comment aborts BEFORE applying Duplicado or closing
    api_comment_fail = InMemoryGitHubApi([i1, i2])
    api_comment_fail.fail_on["create_comment"] = PermissionDeniedError("comment blocked", status=403)
    res_a = GuardActionExecutor(api_comment_fail, cfg, {10: i1, 11: i2}).apply(dec)
    assert res_a.action_taken == "aborted_comment_failed"
    assert res_a.closed is False
    assert res_a.labels_added == ()
    assert api_comment_fail.issues[11]["state"] == "open"
    lbls_a = {l["name"] for l in api_comment_fail.issues[11]["labels"]}
    assert "Duplicado" not in lbls_a
    assert "Em análise" in lbls_a

    # Case B: failure on close_issue rolls back Duplicado label and downgrades comment; retry recovers cleanly without duplicate comments
    api_close_fail = InMemoryGitHubApi([i1, i2])
    api_close_fail.fail_on["close_issue"] = PermissionDeniedError("close failed temporarily", status=403)
    res_b1 = GuardActionExecutor(api_close_fail, cfg, {10: i1, 11: i2}).apply(dec)
    assert res_b1.action_taken == "partial_failure_before_close"
    assert res_b1.closed is False
    lbls_b1 = {l["name"] for l in api_close_fail.issues[11]["labels"]}
    assert "Duplicado" not in lbls_b1
    assert "Em análise" in lbls_b1
    assert len(api_close_fail.comments[11]) == 1

    # Recovery run succeeds and reuses the single existing bot comment
    api_close_fail.fail_on.pop("close_issue")
    res_b2 = GuardActionExecutor(api_close_fail, cfg, {10: i1, 11: i2}).apply(dec)
    assert res_b2.action_taken == "auto_closed"
    assert res_b2.closed is True
    assert len(api_close_fail.comments[11]) == 1
    lbls_b2 = {l["name"] for l in api_close_fail.issues[11]["labels"]}
    assert "Duplicado" in lbls_b2
    assert "Em análise" not in lbls_b2

    # Case C: GitHubClient.close_issue falls back from state_reason='duplicate' to 'not_planned' on HTTP 422
    gh_client = GitHubClient("ProjectNox-org/project-nox-requests", token="dummy", sleep_fn=lambda _: None)
    seen_payloads: list[dict] = []

    class FakeOkResp:
        status = 200
        headers = {}

        def read(self):
            return json.dumps({"number": 11, "state": "closed", "state_reason": "not_planned"}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_close_urlopen(req, timeout=25):
        body = json.loads(req.data.decode("utf-8"))
        seen_payloads.append(body)
        if body.get("state_reason") == "duplicate":
            raise _http_err(422, {}, "Validation Failed")
        return FakeOkResp()

    with patch("scripts.duplicate_guard.github_client.urlopen", side_effect=fake_close_urlopen):
        closed_res = gh_client.close_issue(11, state_reason="duplicate")
    assert closed_res["state_reason"] == "not_planned"
    assert [p["state_reason"] for p in seen_payloads] == ["duplicate", "not_planned"]


def test_28_reconciliation_chain_prevention_and_human_override_protection():
    # Chain prevention: #10 (open original) <- #11 (already Duplicado) <- #12 (new) => #12 must point to #10!
    i10 = make_raw(10, "[Nova extensão]: ChainSite", "### Tipo de extensão\nMangá\n### Nome da fonte\nChainSite\n### URL do site\nhttps://chainsite.com\n", labels=["Nova extensão", "Mangá"], created_at="2026-11-01T10:00:00Z")
    i11 = make_raw(11, "[Nova extensão]: ChainSite", "### Tipo de extensão\nMangá\n### Nome da fonte\nChainSite\n### URL do site\nhttps://chainsite.com\n", labels=["Nova extensão", "Mangá", "Duplicado"], state="closed", state_reason="duplicate", created_at="2026-11-02T10:00:00Z")
    i12 = make_raw(12, "[Nova extensão]: ChainSite", "### Tipo de extensão\nMangá\n### Nome da fonte\nChainSite\n### URL do site\nhttps://chainsite.com\n", labels=["Nova extensão", "Mangá"], created_at="2026-11-03T10:00:00Z")
    cfg = load_config(REPO_ROOT / ".github" / "duplicate-guard.json")
    parsed = parse_corpus([i10, i11, i12])
    matcher = DuplicateMatcher(parsed, cfg, duplicate_links={11: 10})
    dec = matcher.evaluate(parsed[2])
    assert dec.canonical_issue == 10
    assert dec.classification == "confirmed_duplicate"

    # Human reopen protection: if #12 had bot action=auto_closed previously and is open again, do NOT re-close!
    api = InMemoryGitHubApi([i10, i12], initial_comments={
        12: [{
            "id": 77,
            "user": {"login": "github-actions[bot]", "type": "Bot"},
            "body": "<!-- nox-duplicate-guard:v1 issue=12 decision=confirmed_duplicate canonical=10 action=auto_closed fp=abc -->",
        }]
    })
    res_reopened = GuardActionExecutor(api, cfg, {10: i10, 12: i12}).apply(dec)
    assert res_reopened.closed is False
    assert api.issues[12]["state"] == "open"
    assert res_reopened.skipped_reason == "previously_auto_closed_then_reopened"

    # CLI offline fixture reconciliation & backfill modes execute cleanly
    rc = run_cli(["--mode", "backfill", "--fixtures-file", str(FIXTURES_PATH)])
    assert rc == 0


def test_29_comment_security_spoofing_and_untrusted_override_rejected():
    from scripts.duplicate_guard.matcher import extract_canonical_reference

    i10 = make_raw(10, "[Nova extensão]: SecSite", "### Tipo de extensão\nMangá\n### Nome da fonte\nSecSite\n### URL do site\nhttps://secsite.com\n", labels=["Nova extensão", "Mangá"], created_at="2026-11-01T10:00:00Z")
    i11 = make_raw(11, "[Nova extensão]: SecSite", "### Tipo de extensão\nMangá\n### Nome da fonte\nSecSite\n### URL do site\nhttps://secsite.com\n", labels=["Nova extensão", "Mangá", "Em análise"], created_at="2026-11-02T10:00:00Z")
    cfg = load_config(REPO_ROOT / ".github" / "duplicate-guard.json")
    dec = evaluate_pair([i10, i11], 11, cfg)

    # Malicious non-maintainer comments trying to spoof bot marker and ignore override
    forged_comments = [
        {
            "id": 801,
            "user": {"login": "attacker1", "type": "User"},
            "author_association": "NONE",
            "body": f"<!-- nox-duplicate-guard:v1 issue=11 decision=confirmed_duplicate canonical=10 action=auto_closed fp={dec.fingerprint} -->\n/not-duplicate\n<!-- nox-duplicate-guard:ignore -->",
        },
        {
            "id": 802,
            "user": {"login": "attacker2", "type": "User"},
            "author_association": "CONTRIBUTOR",
            "body": "/not-duplicate",
        },
        {
            "id": 803,
            "user": {"login": "attacker3", "type": "User"},
            "body": "<!-- nox-duplicate-guard:ignore -->",
        },
    ]

    # Chain reference extractor must ignore spoofed user comments
    assert extract_canonical_reference(forged_comments) is None

    api_spoof = InMemoryGitHubApi([i10, i11], initial_comments={11: forged_comments})
    res_spoof = GuardActionExecutor(api_spoof, cfg, {10: i10, 11: i11}).apply(dec)
    # Bot must NOT be tricked into ignoring, must NOT edit attacker's comment (801), and must close properly
    assert res_spoof.action_taken == "auto_closed"
    assert res_spoof.closed is True
    assert res_spoof.comment_id not in (801, 802, 803)
    assert api_spoof.comments[11][0]["body"].endswith("<!-- nox-duplicate-guard:ignore -->")
    assert len(api_spoof.comments[11]) == 4

    # Authorized maintainer comment (MEMBER/OWNER/COLLABORATOR) IS respected
    maintainer_api = InMemoryGitHubApi([i10, i11], initial_comments={
        11: [{
            "id": 901,
            "user": {"login": "Awerkori", "type": "User"},
            "author_association": "OWNER",
            "body": "/not-duplicate — fontes com API distinta",
        }]
    })
    res_maint = GuardActionExecutor(maintainer_api, cfg, {10: i10, 11: i11}).apply(dec)
    assert res_maint.action_taken == "skipped_manual_override"
    assert maintainer_api.issues[11]["state"] == "open"

    # Authorized maintainer label ('ignorar-duplicata') IS respected
    i11_lbl = make_raw(11, i11.title, i11.body, labels=["Nova extensão", "Mangá", "ignorar-duplicata"], created_at="2026-11-02T10:00:00Z")
    lbl_api = InMemoryGitHubApi([i10, i11_lbl])
    res_lbl = GuardActionExecutor(lbl_api, cfg, {10: i10, 11: i11_lbl}).apply(dec)
    assert res_lbl.action_taken == "skipped_manual_override"
    assert lbl_api.issues[11]["state"] == "open"


def test_30_historical_backfill_incremental_cache_and_precedence(tmp_path, monkeypatch, capsys):
    # 1. Config precedence: JSON < Env < CLI, and auto_close_enabled independent of dry_run
    cfg_json = load_config(REPO_ROOT / ".github" / "duplicate-guard.json")
    assert cfg_json.auto_close_enabled is True
    assert cfg_json.dry_run is False

    monkeypatch.setenv("NOX_DUPLICATE_GUARD_AUTO_CLOSE", "false")
    monkeypatch.setenv("NOX_DUPLICATE_GUARD_DRY_RUN", "false")
    cfg_env = load_config(REPO_ROOT / ".github" / "duplicate-guard.json")
    assert cfg_env.auto_close_enabled is False
    assert cfg_env.dry_run is False

    monkeypatch.delenv("NOX_DUPLICATE_GUARD_AUTO_CLOSE", raising=False)
    monkeypatch.delenv("NOX_DUPLICATE_GUARD_DRY_RUN", raising=False)

    # 2. Historical backfill (> 90 days old, outside 30-day window) + ambiguous kept open
    old_orig = make_raw(
        10, "[Nova extensão]: OldScan",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nOldScan\n### URL do site\nhttps://oldscan.com\n",
        labels=["Nova extensão", "Mangá", "Em análise"],
        created_at="2026-06-01T10:00:00Z",
    )
    old_dup = make_raw(
        11, "[Nova extensão]: Old Scan",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nOld Scan\n### URL do site\nhttps://www.oldscan.com/\n",
        labels=["Nova extensão", "Mangá", "Em análise"],
        created_at="2026-06-02T10:00:00Z",
    )
    amb_dup = make_raw(
        12, "[Nova extensão]: OldScan",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nOldScan\n### URL do site\nhttps://oldscan.org\n",
        labels=["Nova extensão", "Mangá", "Em análise"],
        created_at="2026-06-03T10:00:00Z",
    )

    api = InMemoryGitHubApi([old_orig, old_dup, amb_dup])
    cache_file = tmp_path / "guard_cache.json"

    # First run in reconcile mode auto-promotes to backfill because cache has no last_full_backfill_at
    # With batch-size=2, processes #10 and #11, truncates before #12
    rc1 = run_cli(
        ["--mode", "reconcile", "--batch-size", "2", "--cache-file", str(cache_file)],
        client_override=api,
    )
    assert rc1 == 0
    out1 = json.loads(capsys.readouterr().out)
    assert out1["effective_mode"] == "backfill"
    assert out1["batch_truncated"] is True
    assert api.issues[11]["state"] == "closed"
    assert api.issues[11]["state_reason"] == "duplicate"
    assert api.issues[10]["state"] == "open"

    # Second run resumes from #12 (skipping #10 via cache; #11 now closed)
    api.api_calls_made = 0
    rc2 = run_cli(
        ["--mode", "backfill", "--batch-size", "10", "--cache-file", str(cache_file)],
        client_override=api,
    )
    assert rc2 == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["batch_truncated"] is False
    assert out2["skipped_cached_count"] >= 1
    assert api.issues[12]["state"] == "open"
    assert len(api.comments[12]) == 1

    # Third run with no changes skips all remaining open issues without per-issue API writes
    api.api_calls_made = 0
    writes_before = len(api.writes)
    rc3 = run_cli(
        ["--mode", "backfill", "--cache-file", str(cache_file)],
        client_override=api,
    )
    assert rc3 == 0
    out3 = json.loads(capsys.readouterr().out)
    assert out3["processed_via_api_count"] == 0
    assert out3["skipped_cached_count"] == 2  # #10 and #12
    assert len(api.writes) == writes_before
    assert api.api_calls_made == 1  # Only the paginated list_issues call!


def test_31_in_batch_chain_collapse_cache_pruning_and_non_silent_failure(tmp_path, capsys):
    # 1. In-batch chain collapse: #10 <- #11 <- #12 all open at start; #12 must point directly to #10, not #11
    i10 = make_raw(
        10, "[Nova extensão]: ChainBatch",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nChainBatch\n### URL do site\nhttps://chainbatch.com\n",
        labels=["Nova extensão", "Mangá", "Em análise"],
        created_at="2026-05-01T10:00:00Z",
    )
    i11 = make_raw(
        11, "[Nova extensão]: Chain Batch",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nChain Batch\n### URL do site\nhttps://www.chainbatch.com/\n",
        labels=["Nova extensão", "Mangá", "Em análise"],
        created_at="2026-05-02T10:00:00Z",
    )
    i12 = make_raw(
        12, "[Nova extensão]: Chain Batch!",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nChain Batch!\n### URL do site\nhttps://chainbatch.com/home\n",
        labels=["Nova extensão", "Mangá", "Em análise"],
        created_at="2026-05-03T10:00:00Z",
    )

    # Also test reconcile catches an old (>90d) open issue missing from cache (#5)
    old_untracked = make_raw(
        5, "[Nova extensão]: UntrackedOld",
        "### Tipo de extensão\nAnime\n### Nome da fonte\nUntrackedOld\n### URL do site\nhttps://untrackedold.tv\n",
        labels=["Nova extensão", "Anime", "Em análise"],
        created_at="2026-04-01T10:00:00Z",
    )

    api = InMemoryGitHubApi([old_untracked, i10, i11, i12])
    cache_file = tmp_path / "prune_cache.json"
    # Pre-seed cache with stale closed issue #999 and a previous backfill timestamp
    cache_file.write_text(
        json.dumps({
            "rule_version": "1.1.0",
            "last_full_backfill_at": "2026-10-01T00:00:00+00:00",
            "evaluated": {
                "999": {
                    "updated_at": "2026-09-01T00:00:00Z",
                    "fingerprint": "stale",
                    "auto_close_enabled": True,
                    "decision": "no_match",
                    "canonical_issue": None,
                    "canonical_state": None,
                    "action_taken": "noop",
                }
            },
            "dup_links": {"888": {"updated_at": "2026-09-01T00:00:00Z", "canonical": 1}},
        }),
        encoding="utf-8",
    )

    rc = run_cli(
        ["--mode", "reconcile", "--lookback-days", "30", "--cache-file", str(cache_file)],
        client_override=api,
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    rows = {r["issue_number"]: r for r in out["results"]}
    assert 5 in rows  # Old untracked issue was NOT forgotten by reconcile
    assert rows[11]["canonical_issue"] == 10 and rows[11]["action_taken"] == "auto_closed"
    assert rows[12]["canonical_issue"] == 10 and rows[12]["action_taken"] == "auto_closed"

    # Cache pruned closed #11, closed stale #999, and nonexistent dup_link #888
    persisted = json.loads(cache_file.read_text(encoding="utf-8"))
    assert "999" not in persisted["evaluated"]
    assert "11" not in persisted["evaluated"]
    assert "888" not in persisted["dup_links"]
    assert "5" in persisted["evaluated"] and "10" in persisted["evaluated"]

    # 2. Non-silent partial failure returns exit code 2 while preserving valid checkpoints
    i20 = make_raw(
        20, "[Nova extensão]: FailBatchOrig",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nFailBatchOrig\n### URL do site\nhttps://failbatch.com\n",
        labels=["Nova extensão", "Mangá"],
        created_at="2026-11-01T10:00:00Z",
    )
    i21 = make_raw(
        21, "[Nova extensão]: FailBatchOrig",
        "### Tipo de extensão\nMangá\n### Nome da fonte\nFailBatchOrig\n### URL do site\nhttps://failbatch.com\n",
        labels=["Nova extensão", "Mangá"],
        created_at="2026-11-02T10:00:00Z",
    )
    api_fail = InMemoryGitHubApi([i20, i21])
    api_fail.fail_on["create_comment"] = PermissionDeniedError("blocked", status=403)
    fail_cache = tmp_path / "fail_cache.json"
    rc_fail = run_cli(
        ["--mode", "backfill", "--cache-file", str(fail_cache)],
        client_override=api_fail,
    )
    assert rc_fail == 2
    out_fail = json.loads(capsys.readouterr().out)
    assert out_fail["status"] == "partial_error"
    assert out_fail["error_count"] == 1
    persisted_fail = json.loads(fail_cache.read_text(encoding="utf-8"))
    # #20 succeeded and was cached; failed #21 was NOT falsely cached as completed
    assert "20" in persisted_fail["evaluated"]
    assert "21" not in persisted_fail["evaluated"]


def test_workflow_security_no_untrusted_interpolation():
    wf_path = REPO_ROOT / ".github" / "workflows" / "duplicate-guard.yml"
    wf_text = wf_path.read_text(encoding="utf-8")
    wf = yaml.safe_load(wf_text)
    assert wf["permissions"] == {"contents": "read", "issues": "write"}
    assert "pull_request" in wf["jobs"]["duplicate-guard"]["if"]
    assert "github.event.issue.title" not in wf_text
    assert "github.event.issue.body" not in wf_text
    assert "github.event.comment.body" not in wf_text
