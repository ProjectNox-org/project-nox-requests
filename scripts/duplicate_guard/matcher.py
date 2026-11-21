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
    INTENT_BUG_FIX,
    INTENT_CREATE_EXTENSION,
    INTENT_DEAD_SOURCE,
    INTENT_DOMAIN_CHANGE,
    INTENT_FEATURE_ADDITION,
    INTENT_LANGUAGE_SUPPORT,
    INTENT_LOADING_ISSUE,
    INTENT_MISSING_CONTENT,
    LABEL_DONE,
    LABEL_DUPLICATE,
    TRUSTED_AUTHOR_ASSOCIATIONS,
    TRUSTED_BOT_LOGINS,
    CandidateEvaluation,
    CatalogExtension,
    ExtensionCatalog,
    GuardConfig,
    GuardDecision,
    ParsedIssue,
)
from .normalize import (
    build_corpus_idf,
    classify_dead_source_evidence,
    compare_source_urls,
    dice_bigrams,
    jaccard_similarity,
    levenshtein_ratio,
    name_without_tld,
    normalize_text,
    overlap_coefficient,
    parse_version,
    strip_accents,
    tfidf_cosine,
    tokenize,
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
    (frozenset({"login_auth"}), frozenset({"no_pages_found", "no_results_found", "video_playback", "slow_download", "missing_chapters", "timeout", "json_decode"})),
    (frozenset({"slow_download"}), frozenset({"json_decode", "nxdomain_dns", "login_auth", "no_pages_found", "missing_chapters"})),
    (frozenset({"video_playback"}), frozenset({"login_auth", "nxdomain_dns"})),
    (frozenset({"timeout"}), frozenset({"json_decode", "null_pointer", "nxdomain_dns", "no_pages_found", "no_results_found", "login_auth", "missing_chapters"})),
    (frozenset({"json_decode"}), frozenset({"timeout", "nxdomain_dns", "ssl_tls", "slow_download", "no_pages_found"})),
    (frozenset({"missing_chapters"}), frozenset({"no_pages_found", "login_auth", "slow_download"})),
    (frozenset({"no_pages_found"}), frozenset({"no_results_found", "login_auth", "slow_download", "missing_chapters", "timeout", "json_decode"})),
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


def _is_trusted_canonical_comment(entry: dict) -> bool:
    user = entry.get("user")
    if not isinstance(user, dict):
        return False
    login = str(user.get("login") or "").strip().lower()
    user_type = str(user.get("type") or "").strip().lower()
    if login in TRUSTED_BOT_LOGINS and (user_type == "bot" or login.endswith("[bot]")):
        return True
    assoc = str(entry.get("author_association") or "").strip().upper()
    return bool(login) and user_type in ("", "user") and assoc in TRUSTED_AUTHOR_ASSOCIATIONS


def extract_canonical_reference(comments: list[dict] | list[str]) -> int | None:
    """Extract canonical issue number referenced in trusted bot or maintainer comments."""
    for entry in comments:
        if isinstance(entry, dict):
            if not _is_trusted_canonical_comment(entry):
                continue
            body = str(entry.get("body") or "")
        else:
            body = str(entry or "")
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


_GENERIC_BUG_SIGNATURES = frozenset({
    "cloudflare",
    "timeout",
    "site_blocked",
    "http:400",
    "http:401",
    "http:403",
    "http:404",
    "http:410",
    "http:429",
    "http:500",
    "http:502",
    "http:503",
    "http:504",
    "http:520",
    "http:521",
    "http:522",
    "http:523",
    "http:525",
    "http:526",
})

_SYMPTOM_INCOMPATIBLE_PAIRS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (frozenset({"wrong_chapters", "chapter_order"}), frozenset({"no_pages_or_empty", "missing_content", "slow_download", "download_fail", "auth_failure", "login_auth"})),
    (frozenset({"vip_paywall"}), frozenset({"cloudflare_block", "no_pages_or_empty", "slow_download", "download_fail", "auth_failure", "login_auth"})),
    (frozenset({"audio_subtitle"}), frozenset({"no_pages_or_empty", "wrong_chapters", "slow_download", "download_fail", "auth_failure", "login_auth"})),
    (frozenset({"site_redesign"}), frozenset({"slow_download", "vip_paywall"})),
    (frozenset({"auth_failure", "login_auth"}), frozenset({"no_pages_or_empty", "missing_content", "wrong_chapters", "download_fail", "slow_download", "audio_subtitle", "cover_thumbnail", "profile_avatar"})),
    (frozenset({"download_fail", "slow_download"}), frozenset({"no_pages_or_empty", "wrong_chapters", "auth_failure", "login_auth", "missing_content", "cover_thumbnail", "profile_avatar"})),
    (frozenset({"missing_content"}), frozenset({"no_pages_or_empty", "wrong_chapters", "chapter_order", "download_fail", "slow_download", "auth_failure", "login_auth"})),
    (frozenset({"cover_thumbnail"}), frozenset({"metadata_text", "no_pages_or_empty", "download_fail", "slow_download", "auth_failure", "wrong_chapters", "profile_avatar"})),
    (frozenset({"profile_avatar"}), frozenset({"cover_thumbnail", "metadata_text", "no_pages_or_empty", "missing_content", "download_fail", "slow_download", "wrong_chapters"})),
)

_STRONG_SPECIFIC_BUG_SIGNATURES = frozenset({
    "no_pages_found",
    "no_results_found",
    "nxdomain_dns",
    "json_decode",
    "null_pointer",
})

_SIGNATURE_CONSTITUENT_TOKENS: dict[str, frozenset[str]] = {
    "no_pages_found": frozenset({"no", "page", "pages", "found", "nenhuma", "pagina", "paginas", "encontrada", "encontradas", "sem"}),
    "no_results_found": frozenset({"no", "result", "results", "found", "nenhum", "resultado", "resultados", "encontrado", "encontrados"}),
    "json_decode": frozenset({"jsondecodingexception", "serializationexception", "json", "invalido", "invalid", "syntaxerror", "erro", "ler", "falha"}),
    "null_pointer": frozenset({"nullpointerexception", "null", "object", "reference", "attempt", "invoke", "virtual", "method", "npe", "java", "lang"}),
    "nxdomain_dns": frozenset({"nxdomain", "unknownhostexception", "resolve", "resolver", "host", "hostname", "conhecido", "endereco"}),
    "ssl_tls": frozenset({"sslhandshakeexception", "certpathvalidatorexception", "ssl", "certificado"}),
    "timeout": frozenset({"sockettimeoutexception", "timeout", "timed", "out", "tempo", "esgotado"}),
    "cloudflare": frozenset({"cloudflare", "turnstile", "challenge", "contornar"}),
    "login_auth": frozenset({"login", "autenticacao", "autenticar", "cadastro", "sessao", "conta", "entrar"}),
    "slow_download": frozenset({"lentidao", "demorando", "demora", "baixar", "download", "lento"}),
    "video_playback": frozenset({"reproduz", "reproduziu", "reproduzir", "video", "carrega", "player"}),
    "site_blocked": frozenset({"site", "bloqueou", "acesso", "bloqueio"}),
}

_ALL_SIG_CONSTITUENT_TOKENS: frozenset[str] = frozenset().union(*_SIGNATURE_CONSTITUENT_TOKENS.values())

_DECLARED_PART_TOKENS: dict[str, frozenset[str]] = {
    "reader": frozenset({"leitor", "imagem", "imagens"}),
    "chapters": frozenset({"capitulo", "capitulos"}),
    "video": frozenset({"video", "videos", "player", "episodio", "episodios"}),
    "search": frozenset({"busca", "pesquisa", "filtro", "filtros"}),
    "catalog": frozenset({"catalogo", "popular", "populares", "recentes"}),
    "login": frozenset({"login", "conta", "autenticacao"}),
    "install": frozenset({"instalacao", "atualizacao"}),
    "details": frozenset({"detalhes", "metadados", "sinopse"}),
    "metadata": frozenset({"detalhes", "metadados", "sinopse"}),
}

_GENERIC_BUG_STOPWORDS = frozenset({
    "extensao", "extensoes", "extension", "fonte", "fontes", "source",
    "manga", "mangas", "anime", "animes", "site", "sites", "app", "aplicativo",
    "erro", "erros", "error", "errors", "problema", "problemas", "bug", "bugs",
    "falha", "falhas", "funciona", "funcionando", "funcionar", "pega", "pegando",
    "abre", "abrir", "abri", "abrindo", "carrega", "carregar", "carregando",
    "aparece", "aparecendo", "mostra", "mostrando", "dando", "deu", "parou",
    "parando", "ficou", "fica", "ficando", "tenta", "tentando", "tentar", "tento",
    "quando", "sempre", "toda", "vez", "jeito", "nenhum", "totalmente", "nada",
    "coisa", "algo", "alguem", "aqui", "hoje", "agora", "ontem", "ultima",
    "ultimo", "ultimos", "novo", "nova", "novos", "versao", "version",
    "http", "https", "status", "code", "codigo", "mensagem", "arquivo", "arquivos",
    "imagem", "imagens", "screenshot", "print", "qualquer", "alguma", "algum",
    "alguns", "algumas", "todas", "todos", "outro", "outra", "outros",
    "acesso", "acessar", "cloudflare", "timeout", "timed", "out", "tempo",
    "esgotado", "bloqueio", "bloqueou", "bloqueado", "webview",
    "mihon", "aniyomi", "komikku", "suwayomi", "reikai", "tadami", "anizen",
    "kotatsu", "tachiyomi", "400", "401", "403", "404", "410", "429",
    "500", "502", "503", "504", "520", "521", "522", "523", "525", "526",
})

_PART_LABEL_WORDS = frozenset({
    "leitor", "capitulo", "capitulos", "pagina", "paginas", "login", "conta",
    "busca", "pesquisa", "filtro", "filtros", "catalogo", "popular", "recentes",
    "video", "episodio", "episodios", "instalacao", "atualizacao", "detalhes",
    "metadados",
})

_SIMPLE_PLURAL_FOLD = {
    "capitulos": "capitulo",
    "paginas": "pagina",
    "episodios": "episodio",
    "imagens": "imagem",
    "obras": "obra",
    "erros": "erro",
    "filtros": "filtro",
}


def _specific_bug_evidence_tokens(
    issue: ParsedIssue,
    *,
    include_part_words: bool = True,
    exclude_signature_tokens: bool = False,
    exclude_declared_part_tokens: bool = False,
) -> frozenset[str]:
    """Return informative non-generic tokens excluding extension name, domain, and optionally signature/part overlap."""
    text = f"{issue.title_symptom or issue.clean_title} {issue.description} {issue.reproduction} {issue.additional_info}"
    raw_tokens = tokenize(text)
    exclude = set(_GENERIC_BUG_STOPWORDS)
    exclude.update(tokenize(issue.name))
    if issue.url and issue.url.host:
        exclude.update(re.findall(r"[a-z0-9]+", issue.url.host.lower()))
    if not include_part_words:
        exclude.update(_PART_LABEL_WORDS)
    if exclude_signature_tokens:
        for sig in issue.error_signatures:
            exclude.update(_SIGNATURE_CONSTITUENT_TOKENS.get(sig, ()))
    if exclude_declared_part_tokens:
        for part in issue.affected_parts:
            exclude.update(_DECLARED_PART_TOKENS.get(part, ()))
    cleaned: set[str] = set()
    for tok in raw_tokens:
        folded = _SIMPLE_PLURAL_FOLD.get(tok, tok)
        if folded not in exclude and tok not in exclude:
            cleaned.add(folded)
    return frozenset(cleaned)


def _split_endpoint_tokens(endpoints: frozenset[str]) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Split extracted endpoint identifiers into (paths, method_bindings, endpoint_hosts)."""
    paths: set[str] = set()
    methods: set[str] = set()
    hosts: set[str] = set()
    for ep in endpoints:
        if ep.startswith("method:"):
            methods.add(ep)
        elif ep.startswith("ephost:"):
            hosts.add(ep)
        else:
            paths.add(ep)
    return frozenset(paths), frozenset(methods), frozenset(hosts)


_TITLE_SUBJECT_GENERIC_NOUNS = frozenset({
    "possivel", "impossivel", "lista", "listagem", "menu", "tela", "aba", "secao",
    "interface", "layout", "modo", "item", "itens", "dados", "carregamento",
    "reproducao", "conexao", "servidor", "resposta", "requisicao", "request",
    "parte", "area", "campo", "conteudo", "conteudos", "info", "informacao",
    "informacoes", "detalhe", "endpoint", "rota", "capitulo", "episodio",
})


def _distinctive_title_subject_tokens(issue: ParsedIssue) -> frozenset[str]:
    ts = (issue.title_symptom or "").strip()
    if not ts:
        return frozenset()
    ext_toks = tokenize(issue.name)
    tech_parts: set[str] = set()
    for t in issue.technical_tokens:
        val = t.split(":", 1)[1] if ":" in t else t
        tech_parts.update(re.findall(r"[a-z0-9]+", val.lower()))
    if issue.url and issue.url.host:
        tech_parts.update(re.findall(r"[a-z0-9]+", issue.url.host.lower()))
    exclude = (
        set(_GENERIC_BUG_STOPWORDS)
        | set(_PART_LABEL_WORDS)
        | set(_ALL_SIG_CONSTITUENT_TOKENS)
        | _TITLE_SUBJECT_GENERIC_NOUNS
        | ext_toks
        | tech_parts
        | {"cap", "ep"}
    )
    out: set[str] = set()
    for tok in tokenize(ts):
        folded = _SIMPLE_PLURAL_FOLD.get(tok, tok)
        if tok.isdigit() or len(folded) < 3:
            continue
        if tok in exclude or folded in exclude:
            continue
        if issue.compact_name and len(folded) >= 4 and (folded in issue.compact_name):
            continue
        if folded.endswith(("exception", "error")):
            continue
        out.add(folded)
    return frozenset(out)


def _title_subjects_diverge(target: ParsedIssue, cand: ParsedIssue) -> tuple[bool, str | None, bool]:
    subj_t = _distinctive_title_subject_tokens(target)
    subj_c = _distinctive_title_subject_tokens(cand)
    if not subj_t and not subj_c:
        return False, None, False
    text_t = {_SIMPLE_PLURAL_FOLD.get(tok, tok) for tok in target.all_text_tokens} | target.all_text_tokens
    text_c = {_SIMPLE_PLURAL_FOLD.get(tok, tok) for tok in cand.all_text_tokens} | cand.all_text_tokens
    missing_in_c = subj_t - text_c
    missing_in_t = subj_c - text_t
    if missing_in_c and missing_in_t:
        return True, "different_title_subjects", True
    if missing_in_c or missing_in_t:
        return True, "unilateral_title_subject", False
    return False, None, False


_COMMENT_DENIAL_RE = re.compile(
    r"\b(?:"
    r"nao\s+e\s+(?:o\s+mesmo|duplicad[oa]|igual|mesma\s+coisa|esse\s+erro)|"
    r"nao\s+(?:trata|se\s+trata)\s+d[oa]\s+mesm[oa]|"
    r"nao\s+tem\s+(?:relacao|a\s+ver)|"
    r"(?:outro|outra)\s+(?:problema|erro|solicitacao|extensao|caso)|"
    r"(?:problema|erro|motivo|caso|sintoma)\s+diferente|"
    r"diferente\s+d[oa]\s+(?:issue|ticket|#?\d+)"
    r")\b",
    re.IGNORECASE,
)

_EXCLUSIVITY_CUE_RE = re.compile(
    r"\b(?:somente|apenas|so|exclusivamente)\s+(?:no|na|ao|para|em)\b",
    re.IGNORECASE,
)


def _is_verifiable_author_or_maintainer_comment(comment: dict, issue_author: str) -> bool:
    if not isinstance(comment, dict):
        return False
    user = comment.get("user")
    if not isinstance(user, dict):
        return False
    login = str(user.get("login") or "").strip()
    login_lower = login.lower()
    user_type = str(user.get("type") or "").strip().lower()
    if not login or user_type == "bot" or login_lower.endswith("[bot]") or login_lower in TRUSTED_BOT_LOGINS:
        return False
    if user_type and user_type != "user":
        return False
    assoc = str(comment.get("author_association") or "").strip().upper()
    if assoc in TRUSTED_AUTHOR_ASSOCIATIONS:
        return True
    author_lower = (issue_author or "").strip().lower()
    if author_lower and author_lower not in ("unknown", "ghost", "deleted", "anonymous"):
        return login_lower == author_lower
    return False


def evaluate_comment_clarifications(
    target: ParsedIssue,
    canonical: ParsedIssue | None,
    comments: list[dict],
    *,
    catalog_ext_summary: dict | None = None,
    match_kind: str = "duplicate",
) -> tuple[bool, str | None]:
    """Check verifiable author/maintainer comments ONLY to block or downgrade unsafe auto-closes.

    Security & asymmetry rule (Sec 8):
    - Only accepts comments from the verified issue author or repo maintainers (OWNER/MEMBER/COLLABORATOR).
    - Ignores bot comments and untrusted third-party user comments.
    - Comment text NEVER promotes a weak match to confirmed_duplicate; it only blocks/downgrades.
    """
    from .normalize import (
        detect_extension_already_exists,
        detect_regression_signal,
        extract_api_endpoints,
        extract_chapter_numbers,
        extract_episode_numbers,
        extract_error_signatures,
        extract_feature_and_symptom_topics,
        extract_language_context,
        extract_specific_work_hint,
        extract_urls_from_text,
    )
    from .parser import _infer_parts_from_free_text

    qualifying_bodies: list[str] = []
    for c in comments:
        if _is_verifiable_author_or_maintainer_comment(c, target.raw.author):
            body = str(c.get("body") or "").strip()
            if body:
                qualifying_bodies.append(body)
    if not qualifying_bodies:
        return False, None

    combined_comments = "\n".join(qualifying_bodies)
    norm_comments = normalize_text(combined_comments, preserve_urls=False)
    if _COMMENT_DENIAL_RE.search(strip_accents(combined_comments).lower()):
        return True, "author_or_maintainer_denied_duplication_in_comment"

    if detect_regression_signal(combined_comments):
        return True, "regression_clarification_in_comment"

    extra_parts = frozenset(_infer_parts_from_free_text(combined_comments))
    extra_sigs, _ = extract_error_signatures(combined_comments)
    extra_feats, extra_symps = extract_feature_and_symptom_topics(combined_comments)
    _, _, extra_langs, extra_existing_only = extract_language_context("", combined_comments)
    extra_exists = detect_extension_already_exists(combined_comments)
    extra_work = extract_specific_work_hint("", combined_comments, extension_name=target.name)
    extra_chapters = extract_chapter_numbers(combined_comments)
    extra_episodes = extract_episode_numbers(combined_comments)
    extra_endpoints = extract_api_endpoints(combined_comments)
    extra_urls = extract_urls_from_text(combined_comments)
    ver_m = re.search(r"\bvers(?:ao|ion)\s+v?(\d+(?:\.\d+)+)\b", norm_comments)
    comment_ver = parse_version(ver_m.group(1)) if ver_m else ()

    if canonical is not None:
        if extra_parts:
            rel = _parts_status(extra_parts, canonical.affected_parts)
            if rel == "contradictory":
                return True, "contradictory_affected_part_in_comment"
            if _EXCLUSIVITY_CUE_RE.search(norm_comments) and (extra_parts - canonical.affected_parts):
                return True, "exclusive_affected_part_clarification_in_comment"
        if extra_symps and canonical.symptom_topics:
            for g1, g2 in _SYMPTOM_INCOMPATIBLE_PAIRS:
                if ((extra_symps & g1) and (canonical.symptom_topics & g2)) or (
                    (canonical.symptom_topics & g1) and (extra_symps & g2)
                ):
                    return True, "contradictory_symptom_in_comment"
        if extra_sigs and _signatures_contradict(target.error_signatures | extra_sigs, canonical.error_signatures):
            return True, "contradictory_error_signature_in_comment"
        if comment_ver and canonical.version_tuple and comment_ver != canonical.version_tuple:
            return True, "version_mismatch_in_comment"
        if extra_work and (not canonical.specific_work_hint or levenshtein_ratio(extra_work.replace(" ", ""), canonical.specific_work_hint.replace(" ", "")) < 0.80):
            return True, "specific_work_clarification_in_comment"
        if extra_chapters and extra_chapters != canonical.mentioned_chapters:
            return True, "chapter_divergence_in_comment"
        if extra_episodes and extra_episodes != canonical.mentioned_episodes:
            return True, "episode_divergence_in_comment"
        if extra_endpoints:
            e_paths, e_methods, e_hosts = _split_endpoint_tokens(extra_endpoints)
            c_paths, c_methods, c_hosts = _split_endpoint_tokens(canonical.api_endpoints)
            if e_paths and c_paths and not (e_paths & c_paths):
                return True, "endpoint_divergence_in_comment"
            if e_paths and not c_paths:
                return True, "endpoint_clarification_in_comment"
            if e_methods and c_methods and not (e_methods & c_methods):
                return True, "http_method_divergence_in_comment"
            if e_hosts and c_hosts and not (e_hosts & c_hosts):
                return True, "endpoint_subdomain_divergence_in_comment"
        if extra_langs and target.category in (CATEGORY_NEW_EXTENSION, CATEGORY_ENHANCEMENT):
            c_langs = canonical.target_languages - {"all"}
            e_langs = extra_langs - {"all"}
            if e_langs and c_langs and not (e_langs & c_langs):
                return True, "language_divergence_in_comment"
        if extra_existing_only and extra_existing_only != canonical.existing_only_languages:
            return True, "existing_language_divergence_in_comment"
        if target.category == CATEGORY_NEW_EXTENSION and (extra_exists or (extra_feats - {"nsfw_adult"})):
            return True, "intent_divergence_in_comment"
        if extra_feats and canonical.feature_topics and target.category == CATEGORY_ENHANCEMENT:
            if not ((extra_feats - {"nsfw_adult"}) <= (canonical.feature_topics - {"nsfw_adult"})):
                return True, "feature_divergence_in_comment"
        if extra_urls and target.category in (CATEGORY_NEW_EXTENSION, CATEGORY_DOMAIN_CHANGE):
            canon_keys = {
                (u.source_identity_key or u.identity_key)
                for u in (canonical.url, canonical.new_url)
                if u and u.is_valid_domain
            }
            extra_keys = {
                (u.source_identity_key or u.identity_key)
                for u in extra_urls
                if u.is_valid_domain
            }
            if canon_keys and extra_keys and not (extra_keys <= canon_keys):
                return True, "domain_or_source_divergence_in_comment"
    elif match_kind == "already_fulfilled_creation":
        if extra_exists or (extra_feats - {"nsfw_adult"}):
            return True, "intent_divergence_in_comment"
        if extra_urls and catalog_ext_summary:
            src_keys = {
                s.get("source_identity_key")
                for s in (catalog_ext_summary.get("sources") or [])
                if isinstance(s, dict) and s.get("source_identity_key")
            }
            extra_keys = {
                (u.source_identity_key or u.identity_key)
                for u in extra_urls
                if u.is_valid_domain
            }
            if src_keys and extra_keys and not (extra_keys <= src_keys):
                return True, "catalog_source_divergence_in_comment"
        if extra_langs and catalog_ext_summary:
            cat_langs = set(catalog_ext_summary.get("matched_languages") or catalog_ext_summary.get("languages") or [])
            if "all" not in cat_langs and not (extra_langs <= cat_langs):
                return True, "unsupported_language_in_comment"
        if _EXCLUSIVITY_CUE_RE.search(norm_comments) or extra_symps or extra_sigs:
            return True, "non_creation_scope_in_comment"

    return False, None


def _bug_scopes_diverge(target: ParsedIssue, cand: ParsedIssue) -> tuple[bool, str | None, bool]:
    """Compare work, title subject, chapter, episode, endpoint, method, source subdomain, and DNS-host scopes.

    Returns (diverges, reason_code, hard_disjoint) where hard_disjoint=True warrants no_match.
    Always evaluate hard-disjoint contradictions before soft unilateral ambiguities.
    """
    if (
        target.url and cand.url
        and target.url.is_valid_domain and cand.url.is_valid_domain
        and target.url.identity_key == cand.url.identity_key
        and target.url.exact_host != cand.url.exact_host
    ):
        return True, "different_source_subdomains", True

    wt = target.specific_work_hint.replace(" ", "")
    wc = cand.specific_work_hint.replace(" ", "")
    if wt and wc and levenshtein_ratio(wt, wc) < 0.80:
        return True, "different_works", True

    paths_t, methods_t, ephosts_t = _split_endpoint_tokens(target.api_endpoints)
    paths_c, methods_c, ephosts_c = _split_endpoint_tokens(cand.api_endpoints)
    shared_endpoints = paths_t & paths_c
    if paths_t and paths_c and not shared_endpoints:
        return True, "different_endpoints", True
    if methods_t and methods_c and not (methods_t & methods_c):
        return True, "different_http_methods", True
    if ephosts_t and ephosts_c and not (ephosts_t & ephosts_c):
        return True, "different_endpoint_subdomains", True

    if target.mentioned_chapters and cand.mentioned_chapters and target.mentioned_chapters != cand.mentioned_chapters:
        return True, "different_chapters", True
    if target.mentioned_episodes and cand.mentioned_episodes and target.mentioned_episodes != cand.mentioned_episodes:
        return True, "different_episodes", True

    if "nxdomain_dns" in (target.error_signatures & cand.error_signatures):
        hosts_t = {t for t in target.technical_tokens if t.startswith("host:")}
        hosts_c = {t for t in cand.technical_tokens if t.startswith("host:")}
        if hosts_t and hosts_c and not (hosts_t & hosts_c):
            return True, "different_dns_hosts", True

    title_div, title_reason, title_hard = _title_subjects_diverge(target, cand)
    if title_div and title_hard and not (wt and wc):
        return True, title_reason, True

    # Soft / unilateral scope ambiguities (keep open as possible_duplicate, never auto-close)
    if bool(wt) != bool(wc):
        return True, "local_vs_global_work", False
    if (bool(wt) or target.has_unnamed_specific_work_scope) and cand.has_global_work_scope and not cand.specific_work_hint:
        return True, "local_vs_global_work", False
    if (bool(wc) or cand.has_unnamed_specific_work_scope) and target.has_global_work_scope and not target.specific_work_hint:
        return True, "local_vs_global_work", False
    if (
        (target.has_unnamed_specific_work_scope or cand.has_unnamed_specific_work_scope)
        and not (wt and wc)
        and not (target.mentioned_chapters and target.mentioned_chapters == cand.mentioned_chapters)
        and not (target.mentioned_episodes and target.mentioned_episodes == cand.mentioned_episodes)
    ):
        return True, "unidentified_specific_scope", False

    has_shared_nx_host = (
        "nxdomain_dns" in (target.error_signatures & cand.error_signatures)
        and bool(
            {t for t in target.technical_tokens if t.startswith("host:")}
            & {t for t in cand.technical_tokens if t.startswith("host:")}
        )
    )
    if bool(paths_t) != bool(paths_c) and not has_shared_nx_host:
        return True, "endpoint_scope_mismatch", False

    if title_div and not (wt and wc):
        return True, title_reason, False

    if target.mentioned_chapters and cand.mentioned_chapters:
        if not (wt and wc) and not shared_endpoints:
            return True, "chapter_without_work_or_endpoint", False
    elif bool(target.mentioned_chapters) != bool(cand.mentioned_chapters):
        return True, "local_vs_global_chapter", False

    if target.mentioned_episodes and cand.mentioned_episodes:
        if not (wt and wc) and not shared_endpoints:
            return True, "episode_without_work_or_endpoint", False
    elif bool(target.mentioned_episodes) != bool(cand.mentioned_episodes):
        return True, "local_vs_global_episode", False

    return False, None, False


def _symptoms_contradict(target: ParsedIssue, cand: ParsedIssue) -> bool:
    st, sc = target.symptom_topics, cand.symptom_topics
    if st and sc:
        for g1, g2 in _SYMPTOM_INCOMPATIBLE_PAIRS:
            if ((st & g1) and (sc & g2) and not (st & g2 and sc & g2)) or (
                (sc & g1) and (st & g2) and not (st & g1 and sc & g1)
            ):
                return True
    wt, wc = target.specific_work_hint, cand.specific_work_hint
    if wt and wc:
        if levenshtein_ratio(wt.replace(" ", ""), wc.replace(" ", "")) < 0.80:
            return True
    return False


def _describe_intent_pt(intent: str) -> str:
    return {
        INTENT_CREATE_EXTENSION: "criação de nova extensão",
        INTENT_LANGUAGE_SUPPORT: "suporte a outro idioma / conteúdo localizado",
        INTENT_MISSING_CONTENT: "inclusão de conteúdo ausente em extensão existente",
        INTENT_FEATURE_ADDITION: "adição de funcionalidade / melhoria",
        INTENT_BUG_FIX: "correção de defeito em extensão existente",
        INTENT_LOADING_ISSUE: "problema de carregamento / reprodução",
        INTENT_DOMAIN_CHANGE: "mudança de domínio",
        INTENT_DEAD_SOURCE: "fonte indisponível / encerrada",
    }.get(intent, intent or "solicitação geral")


def _signed_days_after(later_ts: str | None, earlier_ts: str | None) -> float:
    dt_later = _parse_iso_dt(later_ts)
    dt_earlier = _parse_iso_dt(earlier_ts)
    return (dt_later - dt_earlier).total_seconds() / 86400.0


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
        catalog: ExtensionCatalog | None = None,
    ) -> None:
        self.config = config
        self.by_number: dict[int, ParsedIssue] = {p.raw.number: p for p in issues}
        self.duplicate_links: dict[int, int] = dict(duplicate_links or {})
        self.catalog: ExtensionCatalog = catalog or ExtensionCatalog()
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

    def resolve_canonical_with_status(self, candidate_num: int) -> tuple[int, bool]:
        """Follow duplicate chains deterministically, reporting (resolved_number, cycle_detected)."""
        visited: set[int] = set()
        curr = candidate_num
        while curr not in visited:
            visited.add(curr)
            issue = self.by_number.get(curr)
            if not issue or not issue.is_duplicate_marked:
                return curr, False
            linked = self.duplicate_links.get(curr)
            if linked is not None:
                if linked in visited:
                    return curr, True
                if linked in self.by_number:
                    linked_issue = self.by_number[linked]
                    if _issue_order_key(linked_issue) < _issue_order_key(issue):
                        curr = linked
                        continue
                # Explicit link points to deleted/404/inaccessible canonical: stop conservatively
                return curr, True
            fallback = self._find_fallback_canonical(issue, visited)
            if fallback is not None:
                return fallback, False
            return curr, False
        return candidate_num, True

    def resolve_canonical(self, candidate_num: int) -> int:
        """Follow duplicate chains deterministically without cycles to a valid canonical issue."""
        resolved, _ = self.resolve_canonical_with_status(candidate_num)
        return resolved

    def reindex_issue(self, updated: ParsedIssue) -> None:
        """Update in-memory matcher indexes when a live issue or canonical is edited mid-run."""
        num = updated.raw.number
        old = self.by_number.get(num)
        self.by_number[num] = updated
        if old is not None and old.category != updated.category:
            old_list = self.by_category.get(old.category, [])
            self.by_category[old.category] = [i for i in old_list if i.raw.number != num]
        cat_list = self.by_category.setdefault(updated.category, [])
        replaced = False
        for idx, item in enumerate(cat_list):
            if item.raw.number == num:
                cat_list[idx] = updated
                replaced = True
                break
        if not replaced:
            cat_list.append(updated)
        cat_list.sort(key=_issue_order_key)
        self._tokens_cache[num] = _combined_text_tokens(updated)
        self._idf_by_category[updated.category] = build_corpus_idf(
            [self._tokens_cache[i.raw.number] for i in cat_list]
        )

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

    def _has_intervening_done_fix(self, target: ParsedIssue, cand: ParsedIssue) -> ParsedIssue | None:
        """Return an intermediate 'Concluído' ticket for the same extension closed between cand and target."""
        cand_key = _issue_order_key(cand)
        target_created = _parse_iso_dt(target.raw.created_at)
        for mid in self.by_category.get(CATEGORY_BUG, ()):
            if mid.raw.number in (target.raw.number, cand.raw.number):
                continue
            if not (mid.is_closed and mid.resolution == "done" and not mid.is_duplicate_marked):
                continue
            if _issue_order_key(mid) <= cand_key:
                continue
            mid_closed = _parse_iso_dt(mid.raw.closed_at or mid.raw.created_at)
            if mid_closed > target_created:
                continue
            if not _same_parsed_extension_name(target, mid):
                continue
            if _parts_status(target.affected_parts, mid.affected_parts) != "contradictory":
                return mid
        return None

    def _catalog_has_newer_release(self, cand: ParsedIssue, target: ParsedIssue) -> CatalogExtension | None:
        """Return a catalog entry with version strictly newer than the closed candidate bug version."""
        if not self.catalog or not cand.version_tuple or not cand.is_closed:
            return None
        days = _signed_days_after(target.raw.created_at, cand.raw.closed_at or cand.raw.created_at)
        if days <= 0:
            return None
        for ext in self.catalog.find_by_name_and_type(cand.compact_name, cand.ext_type):
            if ext.version_tuple and ext.version_tuple > cand.version_tuple:
                return ext
        return None

    def _collect_distinct_context_notes(self, target: ParsedIssue) -> list[str]:
        """Collect explanatory notes when same source exists historically with distinct intent/context."""
        notes: list[str] = []
        target_key = _issue_order_key(target)
        if target.category == CATEGORY_NEW_EXTENSION:
            for cand in self.by_category.get(CATEGORY_NEW_EXTENSION, ()):
                if cand.raw.number == target.raw.number or _issue_order_key(cand) >= target_key:
                    continue
                if _types_contradict(target, cand):
                    continue
                same_dom = bool(
                    target.url and target.url.is_valid_domain
                    and cand.url and cand.url.is_valid_domain
                    and target.url.identity_key == cand.url.identity_key
                )
                if not same_dom and not _same_parsed_extension_name(target, cand):
                    continue
                is_create_t = target.semantic_intent == INTENT_CREATE_EXTENSION and not target.extension_already_exists
                is_create_c = cand.semantic_intent == INTENT_CREATE_EXTENSION and not cand.extension_already_exists
                if is_create_t != is_create_c or target.semantic_intent != cand.semantic_intent:
                    notes.append(
                        f"Relação histórica com #{cand.raw.number} ignorada para fechamento: os tickets possuem objetivos distintos "
                        f"(#{cand.raw.number}: {_describe_intent_pt(cand.semantic_intent)}; #{target.raw.number}: {_describe_intent_pt(target.semantic_intent)})."
                    )
                    break
        return notes

    def _is_equivalent_creation_request(self, target: ParsedIssue, cand: ParsedIssue) -> bool:
        """Return True only when two creation tickets ask for the exact same source URL/path, name, type, and language."""
        if target.category != CATEGORY_NEW_EXTENSION or cand.category != CATEGORY_NEW_EXTENSION:
            return False
        if (
            target.semantic_intent != INTENT_CREATE_EXTENSION
            or cand.semantic_intent != INTENT_CREATE_EXTENSION
            or target.extension_already_exists
            or cand.extension_already_exists
            or target.category_mismatch
            or cand.category_mismatch
            or target.has_substantive_observations
            or cand.has_substantive_observations
            or bool(target.symptom_topics)
            or bool(cand.symptom_topics)
            or _types_contradict(target, cand)
            or target.ext_type != cand.ext_type
            or target.ext_type is None
        ):
            return False
        if any(l.startswith("unknown:") for l in (target.target_languages | cand.target_languages)):
            return False
        if target.existing_only_languages != cand.existing_only_languages:
            return False
        lang_t = target.target_languages - {"all"}
        lang_c = cand.target_languages - {"all"}
        if ("all" in target.target_languages) != ("all" in cand.target_languages):
            return False
        if bool(target.target_languages) != bool(cand.target_languages):
            return False
        if lang_t and lang_c and not (lang_t <= lang_c):
            return False
        urls_equiv, _ = compare_source_urls(
            target.url,
            cand.url,
            ext_type_a=target.ext_type,
            ext_type_b=cand.ext_type,
            allow_default_type_landing=True,
        )
        if not urls_equiv:
            return False
        t_name, c_name = target.compact_name, cand.compact_name
        name_exact = bool(t_name and c_name and (t_name == c_name or name_without_tld(target.name) == name_without_tld(cand.name)))
        name_ratio = levenshtein_ratio(t_name, c_name) if (t_name and c_name) else 0.0
        stem_compat = False
        if target.url and target.url.host_stem:
            stem_compat = (
                levenshtein_ratio(t_name, target.url.host_stem) >= 0.65
                and levenshtein_ratio(c_name, target.url.host_stem) >= 0.65
            ) or (target.url.host_stem in t_name and target.url.host_stem in c_name)
        return name_exact or name_ratio >= self.config.thresholds.new_extension_confirmed_name_edit_ratio or stem_compat

    def evaluate(self, target: ParsedIssue) -> GuardDecision:
        """Evaluate target issue against older candidates and official published catalogs."""
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
                semantic_intent=target.semantic_intent,
                inferred_category=target.inferred_category,
                category_mismatch=target.category_mismatch,
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
                semantic_intent=target.semantic_intent,
                inferred_category=target.inferred_category,
                category_mismatch=target.category_mismatch,
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

        # Also check inferred category when user filed in the wrong GitHub form
        if target.category_mismatch and target.inferred_category in ALL_CATEGORIES and target.inferred_category != target.category:
            for cand in self.by_category.get(target.inferred_category, ()):
                if cand.raw.number == target.raw.number or _issue_order_key(cand) >= target_key:
                    continue
                ev = self._evaluate_pair(target, cand, resolve_chain=True, compare_category=target.inferred_category)
                if ev is not None and ev.classification != "no_match":
                    matches.append(ev)

        # Check official extension catalogs using unified source-level identity & per-source language coverage
        cat_matches: tuple[CatalogExtension, ...] = ()
        if target.category == CATEGORY_NEW_EXTENSION and self.catalog:
            cat_matches = self.catalog.find_matching_extensions(target)

        if cat_matches:
            cat_ok, cat_block_reason, verified_ext, matched_srcs = self.catalog.verify_issue_fulfillment(target)
            if cat_ok and verified_ext is not None and matched_srcs:
                # Prefer linking to an earlier equivalent creation ticket ONLY if same source URL, name, type, and language
                prior_canon: int | None = None
                for cand in self.by_category.get(CATEGORY_NEW_EXTENSION, ()):
                    if cand.raw.number == target.raw.number or _issue_order_key(cand) >= target_key:
                        continue
                    canon_candidate, cycle_or_missing = self.resolve_canonical_with_status(cand.raw.number)
                    if cycle_or_missing:
                        continue
                    canon_obj = self.by_number.get(canon_candidate, cand)
                    if _issue_order_key(canon_obj) >= target_key:
                        continue
                    if canon_obj.resolution in ("declined", "cancelled"):
                        continue
                    if self._is_equivalent_creation_request(target, canon_obj):
                        prior_canon = canon_candidate
                        break

                should_close = True
                blocked: str | None = None
                if not self.config.auto_close_enabled:
                    should_close = False
                    blocked = "auto_close_disabled"
                elif target.category not in self.config.auto_close_categories:
                    should_close = False
                    blocked = "category_auto_close_disabled"
                elif target.is_closed:
                    should_close = False
                    blocked = "target_already_closed"

                matched_langs = sorted(frozenset().union(*(s.languages for s in matched_srcs)))
                lang_desc = ", ".join(matched_langs) if matched_langs else "N/D"
                ver_desc = f" v{verified_ext.version}" if verified_ext.version else ""
                reason = (
                    f"A fonte '{verified_ext.name}' ({verified_ext.pkg}{ver_desc}, URL '{matched_srcs[0].base_url}', idioma(s): {lang_desc}) "
                    f"já existe e está publicada no catálogo oficial ({verified_ext.catalog_repo})."
                )
                if prior_canon is not None:
                    reason += f" Pedido original equivalente registrado no ticket #{prior_canon}."
                res_label = LABEL_DUPLICATE if prior_canon is not None else LABEL_DONE
                state_reason = "duplicate" if prior_canon is not None else "completed"
                cat_summary = verified_ext.to_summary_dict()
                cat_summary["matched_languages"] = matched_langs
                return GuardDecision(
                    issue_number=target.raw.number,
                    category=target.category,
                    classification="confirmed_duplicate",
                    canonical_issue=prior_canon,
                    candidates=(),
                    reasons=(reason,),
                    fingerprint=target.content_fingerprint,
                    should_auto_close=should_close,
                    blocked_reason=blocked,
                    match_kind="already_fulfilled_creation",
                    semantic_intent=target.semantic_intent,
                    inferred_category=target.inferred_category,
                    category_mismatch=target.category_mismatch,
                    catalog_extension=cat_summary,
                    resolution_label=res_label,
                    close_state_reason=state_reason,
                )

            # Catalog has a related entry on the domain, but source URL/path, per-source language, metadata, or intent does NOT cover target
            if not matches:
                ref_ext = verified_ext or cat_matches[0]
                if matched_srcs:
                    src_langs = sorted(frozenset().union(*(s.languages for s in matched_srcs)))
                    lang_desc = ", ".join(src_langs) if src_langs else "N/D"
                else:
                    lang_desc = ", ".join(sorted(ref_ext.languages)) if ref_ext.languages else "N/D"
                cat_summary = ref_ext.to_summary_dict()
                if matched_srcs:
                    cat_summary["matched_languages"] = sorted(frozenset().union(*(s.languages for s in matched_srcs)))

                if cat_block_reason == "catalog_source_url_or_path_mismatch":
                    inferred_cat = target.inferred_category or target.category
                    mismatch = target.category_mismatch
                    match_kind = "distinct_source_scope"
                    req_url = target.url.raw if target.url else "N/D"
                    reason = (
                        f"A extensão '{ref_ext.name}' existe no catálogo oficial ({ref_ext.catalog_repo}) para outro subdomínio/caminho/porta, "
                        f"não comprovando cobertura para a fonte solicitada ({req_url}); solicitação preservada aberta."
                    )
                elif cat_block_reason == "catalog_source_language_mismatch":
                    req_langs = ", ".join(sorted(target.target_languages)) or "solicitado"
                    inferred_cat = CATEGORY_ENHANCEMENT
                    mismatch = True
                    match_kind = "distinct_objective"
                    reason = (
                        f"A fonte correspondente em '{ref_ext.name}' no catálogo oficial ({ref_ext.catalog_repo}) cobre apenas idioma(s) [{lang_desc}], "
                        f"não cobrindo [{req_langs}]; solicitação preservada aberta."
                    )
                elif cat_block_reason in ("catalog_incomplete_or_contradictory_metadata", "live_catalog_ambiguous_multiple_packages"):
                    inferred_cat = target.inferred_category or target.category
                    mismatch = target.category_mismatch
                    match_kind = "ambiguous_catalog"
                    reason = (
                        f"Metadados do catálogo oficial para '{ref_ext.name}' ({ref_ext.catalog_repo}) são incompletos, contraditórios ou ambíguos ({cat_block_reason}); solicitação preservada aberta."
                    )
                else:
                    inferred_cat = target.inferred_category or target.category
                    mismatch = target.category_mismatch
                    match_kind = "distinct_objective"
                    reason = (
                        f"A extensão '{ref_ext.name}' já existe no catálogo oficial ({ref_ext.catalog_repo}, idioma(s): {lang_desc}), "
                        f"mas esta solicitação trata de {_describe_intent_pt(target.semantic_intent)} e foi preservada aberta."
                    )
                return GuardDecision(
                    issue_number=target.raw.number,
                    category=target.category,
                    classification="no_match",
                    canonical_issue=None,
                    candidates=(),
                    reasons=(reason,),
                    fingerprint=target.content_fingerprint,
                    should_auto_close=False,
                    blocked_reason=cat_block_reason,
                    match_kind=match_kind,
                    semantic_intent=target.semantic_intent,
                    inferred_category=inferred_cat,
                    category_mismatch=mismatch,
                    catalog_extension=cat_summary,
                )

        if not matches:
            notes = self._collect_distinct_context_notes(target)
            reasons = tuple(notes) if notes else ("Nenhuma solicitação equivalente ou semelhante encontrada.",)
            return GuardDecision(
                issue_number=target.raw.number,
                category=target.category,
                classification="no_match",
                canonical_issue=None,
                candidates=(),
                reasons=reasons,
                fingerprint=target.content_fingerprint,
                should_auto_close=False,
                match_kind="distinct_objective" if notes else "none",
                semantic_intent=target.semantic_intent,
                inferred_category=target.inferred_category,
                category_mismatch=target.category_mismatch,
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
            and not canonical_obj.resolution
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
            match_kind=top.match_kind,
            semantic_intent=target.semantic_intent,
            inferred_category=target.inferred_category,
            category_mismatch=target.category_mismatch,
            catalog_extension=cat_matches[0].to_summary_dict() if cat_matches else None,
        )

    def _evaluate_pair(
        self,
        target: ParsedIssue,
        cand: ParsedIssue,
        *,
        resolve_chain: bool,
        compare_category: str | None = None,
    ) -> CandidateEvaluation | None:
        if _types_contradict(target, cand):
            return None

        eff_cat = compare_category or target.category
        if eff_cat == CATEGORY_NEW_EXTENSION:
            res = self._match_new_extension(target, cand)
        elif eff_cat == CATEGORY_BUG:
            res = self._match_bug(target, cand)
        elif eff_cat == CATEGORY_DOMAIN_CHANGE:
            res = self._match_domain_change(target, cand)
        elif eff_cat == CATEGORY_DEAD_SOURCE:
            res = self._match_dead_source(target, cand)
        elif eff_cat == CATEGORY_ENHANCEMENT:
            res = self._match_enhancement(target, cand)
        else:
            return None

        if res is None:
            return None

        match_kind = "duplicate"
        if len(res) == 5:
            classification, score, reasons, contradictions, match_kind = res
        else:
            classification, score, reasons, contradictions = res

        cycle_or_missing_canon = False
        if resolve_chain:
            canonical_num, cycle_or_missing_canon = self.resolve_canonical_with_status(cand.raw.number)
        else:
            canonical_num = cand.raw.number
        canon_obj = self.by_number.get(canonical_num, cand)

        # If target points back to itself or canonical is somehow newer, reject safely
        if canonical_num == target.raw.number or _issue_order_key(canon_obj) >= _issue_order_key(target):
            return None

        # Never auto-close when chain contains a cycle, missing canonical (404), or closed/resolved canonical
        if classification == "confirmed_duplicate" and (
            cycle_or_missing_canon
            or canon_obj.is_duplicate_marked
            or not canon_obj.is_open
            or canon_obj.resolution in ("declined", "cancelled", "done", "duplicate")
        ):
            classification = "possible_duplicate"
            match_kind = "historical_related"
            reasons = (*reasons, "O ticket original encontra-se encerrado, inválido ou em ciclo; marcada revisão humana.")

        return CandidateEvaluation(
            candidate_number=cand.raw.number,
            canonical_number=canonical_num,
            classification=classification,
            score=score,
            reasons=reasons,
            contradictions=contradictions,
            candidate_is_open=canon_obj.is_open,
            candidate_resolution=canon_obj.resolution,
            match_kind=match_kind,
        )

    def _match_new_extension(self, target: ParsedIssue, cand: ParsedIssue):
        t_url, c_url = target.url, cand.url
        t_name, c_name = target.compact_name, cand.compact_name
        th = self.config.thresholds

        # Semantic protection: never merge initial extension creation with enhancement,
        # language-support, existing-extension improvement, or bug fix requests.
        is_create_t = (target.semantic_intent == INTENT_CREATE_EXTENSION) and (not target.extension_already_exists)
        is_create_c = (cand.semantic_intent == INTENT_CREATE_EXTENSION) and (not cand.extension_already_exists)
        if is_create_t != is_create_c or target.semantic_intent != cand.semantic_intent:
            return None

        # Distinct or unknown requested languages on the same domain/site => never close automatically
        if any(lang.startswith("unknown:") for lang in (target.target_languages | cand.target_languages)):
            return None
        lang_t = target.target_languages - {"all"}
        lang_c = cand.target_languages - {"all"}
        if lang_t and lang_c and not (lang_t & lang_c):
            return None
        if target.existing_only_languages != cand.existing_only_languages:
            return None

        language_scope_incompatible = (
            ("all" in target.target_languages) != ("all" in cand.target_languages)
            or (bool(lang_t) and bool(lang_c) and not (lang_t <= lang_c))
            or (bool(target.target_languages) != bool(cand.target_languages))
        )

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
                    "ambiguous_same_source",
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
                    "ambiguous_same_source",
                )
            return None

        if same_domain:
            same_known_type = bool(target.ext_type and cand.ext_type and target.ext_type == cand.ext_type)
            name_compatible = (
                name_exact
                or name_ratio >= th.new_extension_confirmed_name_edit_ratio
                or stem_compat
            )
            urls_equiv, url_div_reason = compare_source_urls(
                t_url,
                c_url,
                ext_type_a=target.ext_type,
                ext_type_b=cand.ext_type,
                allow_default_type_landing=True,
            )
            if not urls_equiv:
                return (
                    "possible_duplicate",
                    0.68,
                    (
                        f"Mesmo domínio registrável ({t_url.registrable_domain}), porém destino específico diverge ({url_div_reason}: {t_url.raw} vs {c_url.raw}); requer revisão humana.",
                    ),
                    (url_div_reason or "subdomain_or_port_divergence",),
                    "ambiguous_same_source",
                )
            # Divergent substantive observations or language superset on creation tickets block automatic closure
            if language_scope_incompatible:
                return (
                    "possible_duplicate",
                    0.70,
                    (
                        f"Mesmo domínio ({t_url.identity_key.split(':', 1)[-1]}), porém a solicitação pede cobertura de idioma não garantida pelo ticket anterior.",
                    ),
                    ("language_scope_divergence",),
                    "ambiguous_same_source",
                )
            if target.has_substantive_observations or cand.has_substantive_observations:
                feat_t = target.feature_topics - {"nsfw_adult"}
                feat_c = cand.feature_topics - {"nsfw_adult"}
                if feat_t and feat_c and not (feat_t & feat_c):
                    return None
                if (target.has_substantive_observations != cand.has_substantive_observations) or (
                    feat_t != feat_c
                ):
                    return (
                        "possible_duplicate",
                        0.72,
                        (
                            f"Mesmo tipo e domínio ({t_url.identity_key.split(':', 1)[-1]}), porém há observações/requisitos específicos divergentes; requer revisão humana.",
                        ),
                        ("scope_or_observation_divergence",),
                        "ambiguous_same_source",
                    )

            if same_known_type and name_compatible:
                if cand.is_open:
                    return (
                        "confirmed_duplicate",
                        0.98 if name_exact else 0.93,
                        (
                            f"Mesmo tipo ({target.ext_type}), mesmo domínio normalizado ({t_url.identity_key.split(':', 1)[-1]}), nome equivalente e mesmo objetivo de criação inicial.",
                        ),
                        (),
                        "duplicate",
                    )
                return (
                    "possible_duplicate",
                    0.82,
                    (
                        f"Mesmo tipo ({target.ext_type}) e domínio ({t_url.identity_key.split(':', 1)[-1]}) encontrados em solicitação anterior já encerrada (sem verificação direta de catálogo para encerramento automático).",
                    ),
                    ("candidate_closed",),
                    "historical_related",
                )
            if same_known_type:
                return (
                    "possible_duplicate",
                    0.75,
                    (f"Mesmo domínio normalizado ({t_url.identity_key.split(':', 1)[-1]}) com nome divergente.",),
                    ("name_divergence",),
                    "ambiguous_same_source",
                )
            if not target.ext_type or not cand.ext_type:
                return (
                    "possible_duplicate",
                    0.70,
                    ("Mesmo domínio normalizado; tipo Mangá/Anime ausente em um dos tickets.",),
                    (),
                    "ambiguous_same_source",
                )

        # Missing URL on at least one side: never confirmed automatically
        if name_exact and len(t_name) >= 4 and cand.is_open:
            return (
                "possible_duplicate",
                0.60,
                ("Mesmo nome da fonte, mas sem URL validada em ambos os tickets para confirmação automática.",),
                ("missing_url",),
                "ambiguous_same_source",
            )
        return None

    def _match_bug(self, target: ParsedIssue, cand: ParsedIssue):
        if not _same_parsed_extension_name(target, cand):
            return None

        # Semantic protection: never confuse bug fix with feature/language request
        non_bug_intents = {INTENT_FEATURE_ADDITION, INTENT_LANGUAGE_SUPPORT, INTENT_MISSING_CONTENT}
        if (target.semantic_intent in non_bug_intents) != (cand.semantic_intent in non_bug_intents):
            return None

        th = self.config.thresholds
        parts_rel = _parts_status(target.affected_parts, cand.affected_parts)
        shared_sigs = target.error_signatures & cand.error_signatures
        shared_tech = target.technical_tokens & cand.technical_tokens
        shared_specific_exc = frozenset(t for t in shared_tech if t.startswith("exc:"))
        shared_hosts = frozenset(t for t in shared_tech if t.startswith("host:"))
        paths_t, _, _ = _split_endpoint_tokens(target.api_endpoints)
        paths_c, _, _ = _split_endpoint_tokens(cand.api_endpoints)
        shared_endpoints = paths_t & paths_c
        symptoms_conflict = _symptoms_contradict(target, cand)
        scope_diverges, scope_reason, scope_hard_disjoint = _bug_scopes_diverge(target, cand)

        if _signatures_contradict(target.error_signatures, cand.error_signatures):
            return None

        if target.error_signatures and cand.error_signatures and not shared_sigs:
            return None

        if parts_rel == "contradictory":
            return None

        if scope_diverges and scope_hard_disjoint:
            return None

        # Regression and recurrent incident protection against closed or previously resolved bugs
        if cand.is_closed:
            if cand.resolution in ("declined", "cancelled"):
                return None
            if target.version_tuple and cand.version_tuple and target.version_tuple > cand.version_tuple:
                return None
            if target.has_regression_signal and cand.resolution == "done":
                return None
            if self._catalog_has_newer_release(cand, target) is not None:
                return None
            ref_closed = cand.raw.closed_at or cand.raw.created_at
            days_since_closed = _signed_days_after(target.raw.created_at, ref_closed)
            if days_since_closed >= self.config.bug_regression_days or days_since_closed < 0:
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
            if cand.is_open and parts_rel == "same" and text_sim >= 0.35 and not symptoms_conflict:
                return (
                    "possible_duplicate",
                    round(min(0.65, text_sim + 0.2), 3),
                    ("Mesma extensão e parte afetada, mas descrição insuficiente para encerramento automático.",),
                    ("insufficient_details",),
                    "ambiguous_same_source",
                )
            return None

        # Never auto-close against closed bugs even if < bug_regression_days
        if cand.is_closed:
            if parts_rel in ("same", "adjacent") and not symptoms_conflict and (shared_sigs or text_sim >= 0.55):
                return (
                    "possible_duplicate",
                    round(min(0.75, text_sim + 0.15), 3),
                    ("Mesma extensão e sintoma semelhante a um ticket recém-fechado; verificar se trata da mesma ocorrência ou nova regressão.",),
                    ("candidate_closed",),
                    "regression_check",
                )
            return None

        # If an intermediate ticket was already closed as Concluído between cand and target, treat cautiously as regression
        intervening_fix = self._has_intervening_done_fix(target, cand)
        if intervening_fix is not None:
            if parts_rel in ("same", "adjacent") and (shared_sigs or text_sim >= 0.50):
                return (
                    "possible_duplicate",
                    round(min(0.72, text_sim + 0.15), 3),
                    (
                        f"Mesma extensão e sintoma semelhante ao ticket #{cand.raw.number}, porém houve correção intermediária (#{intervening_fix.raw.number}); preservado aberto para confirmar possível regressão.",
                    ),
                    ("intervening_fix",),
                    "regression_check",
                )
            return None

        # Explicit regression cue in target text -> never auto-close as duplicate of an older ticket
        if target.has_regression_signal:
            if parts_rel in ("same", "adjacent") and (shared_sigs or text_sim >= 0.45):
                return (
                    "possible_duplicate",
                    round(min(0.70, text_sim + 0.15), 3),
                    ("Relato indica possível regressão/recorrência após funcionamento anterior; preservado aberto para análise.",),
                    ("regression_signal",),
                    "regression_check",
                )
            return None

        # Distinct specific symptom topics or diverging scopes (work, chapter, episode, endpoint) NEVER auto-close
        if symptoms_conflict or scope_diverges:
            if parts_rel in ("same", "adjacent") and (text_sim >= 0.45 or shared_sigs):
                reason_txt = (
                    f"Mesma extensão, porém os relatos diferem em escopo específico ({scope_reason}); preservado aberto para triagem."
                    if scope_diverges
                    else "Mesma extensão e parte afetada, porém relatos indicam sintomas ou obras distintas; preservado para revisão."
                )
                return (
                    "possible_duplicate",
                    round(min(0.68, text_sim + 0.1), 3),
                    (reason_txt,),
                    (scope_reason or "symptom_divergence",),
                    "ambiguous_same_source",
                )
            return None

        # Explicit version mismatch or distant recurring incidents NEVER auto-close (Scenario D)
        version_mismatch = bool(target.version_tuple and cand.version_tuple and target.version_tuple != cand.version_tuple)
        days_apart = _days_between(target.raw.created_at, cand.raw.created_at)
        if version_mismatch:
            return (
                "possible_duplicate",
                round(min(0.76, max(0.55, text_sim + 0.2)), 3),
                (f"Mesma extensão ({target.name}) com sintomas semelhantes, porém em versões distintas da extensão ({cand.version_raw} vs {target.version_raw}); preservado aberto para verificar regressão.",),
                ("version_mismatch",),
                "regression_check",
            )
        if days_apart > 30.0:
            return (
                "possible_duplicate",
                round(min(0.75, max(0.52, text_sim + 0.18)), 3),
                (f"Mesma extensão ({target.name}) com sintoma semelhante em data distante ({int(days_apart)} dias de diferença); preservado aberto para verificar recorrência.",),
                ("distant_dates_recurrence",),
                "regression_check",
            )

        # Unilateral technical signature (only one side reports a specific signature) never auto-closes
        if bool(target.error_signatures) != bool(cand.error_signatures):
            if parts_rel in ("same", "adjacent") and text_sim >= 0.40:
                return (
                    "possible_duplicate",
                    round(min(0.70, text_sim + 0.12), 3),
                    ("Mesma extensão, porém apenas um dos tickets informa assinatura técnica específica de erro; mantido aberto para revisão.",),
                    ("unilateral_error_signature",),
                    "ambiguous_same_source",
                )
            return None

        # Positive specific equivalence requirements for confirmed_duplicate in bugs (v1.5.0 independent evidence):
        # Signature words (e.g. 'pages', 'found' from 'No pages found') and declared part names ('leitor')
        # MUST NOT be double-counted as independent context tokens.
        # Shared hostnames (host:*) and generic exceptions (gexc:*) NEVER authorize auto-close alone.
        shared_indep = (
            _specific_bug_evidence_tokens(
                target,
                include_part_words=True,
                exclude_signature_tokens=True,
                exclude_declared_part_tokens=True,
            )
            & _specific_bug_evidence_tokens(
                cand,
                include_part_words=True,
                exclude_signature_tokens=True,
                exclude_declared_part_tokens=True,
            )
        )
        shared_specific_strict = (
            _specific_bug_evidence_tokens(
                target,
                include_part_words=False,
                exclude_signature_tokens=True,
                exclude_declared_part_tokens=True,
            )
            & _specific_bug_evidence_tokens(
                cand,
                include_part_words=False,
                exclude_signature_tokens=True,
                exclude_declared_part_tokens=True,
            )
        )
        strong_specific_sigs = shared_sigs & _STRONG_SPECIFIC_BUG_SIGNATURES
        has_nxdomain_host = bool("nxdomain_dns" in strong_specific_sigs and shared_hosts)
        # Non-exception shared tokens count so shared JsonDecodingException alone cannot skip context verification
        exc_tok_names = {t.split(":", 1)[1] for t in shared_specific_exc}
        non_exc_indep = shared_indep - exc_tok_names
        has_specific_tech_evidence = bool(strong_specific_sigs or shared_specific_exc)

        has_shared_scope = bool(
            (target.specific_work_hint and cand.specific_work_hint and not _bug_scopes_diverge(target, cand)[0])
            or (target.mentioned_chapters and target.mentioned_chapters == cand.mentioned_chapters)
            or (target.mentioned_episodes and target.mentioned_episodes == cand.mentioned_episodes)
        )
        same_version = bool(target.version_tuple and cand.version_tuple and target.version_tuple == cand.version_tuple)

        desc_len_t = len(normalize_text(f"{target.clean_title} {target.description}", preserve_urls=False))
        desc_len_c = len(normalize_text(f"{cand.clean_title} {cand.description}", preserve_urls=False))

        # Tier 1: Strong specific technical signature or discriminative exception + compatible part + independent context
        if has_specific_tech_evidence and (parts_rel in ("same", "adjacent") or has_nxdomain_host):
            context_ok = (
                bool(shared_endpoints)
                or has_nxdomain_host
                or has_shared_scope
                or (
                    len(shared_indep) >= 2
                    and (not shared_specific_exc or len(non_exc_indep) >= 2)
                    and (
                        text_sim >= th.bug_confirmed_structured_text_similarity
                        or "no_pages_found" in strong_specific_sigs
                        or "nxdomain_dns" in strong_specific_sigs
                    )
                )
                or (
                    "no_pages_found" in strong_specific_sigs
                    and len(shared_indep) >= 1
                    and parts_rel == "same"
                    and same_version
                )
            )
            if context_ok:
                ev_parts = sorted(strong_specific_sigs | shared_specific_exc | shared_endpoints) or (
                    sorted(shared_hosts) if has_nxdomain_host else []
                )
                sigs_str = ", ".join(ev_parts)
                return (
                    "confirmed_duplicate",
                    round(min(0.98, max(0.88, text_sim + 0.35)), 3),
                    (
                        f"Mesma extensão ({target.name}), parte afetada e escopo compatíveis, e evidências independentes equivalentes no mesmo incidente ({sigs_str}).",
                    ),
                    (),
                    "duplicate",
                )

        # Tier 2: Descriptive-only or generic HTTP/Cloudflare bug requires concrete matching part, tight window, and rich shared specifics
        rich_descriptive_match = (
            bool(shared_endpoints and len(shared_specific_strict) >= 4)
            or (text_sim >= th.bug_confirmed_text_similarity and len(shared_specific_strict) >= 4)
            or (days_apart <= 7.0 and text_sim >= 0.65 and len(shared_specific_strict) >= 5)
        )
        if (
            not has_specific_tech_evidence
            and parts_rel == "same"
            and days_apart <= 14.0
            and desc_len_t >= 40
            and desc_len_c >= 40
            and rich_descriptive_match
        ):
            return (
                "confirmed_duplicate",
                round(min(0.96, text_sim + 0.08), 3),
                (
                    f"Mesma extensão ({target.name}), mesma parte afetada e detalhes técnicos específicos equivalentes no mesmo incidente.",
                ),
                (),
                "duplicate",
            )

        # Possible duplicate alerts (human review only — includes generic HTTP errors and high similarity without specifics)
        if shared_sigs <= _GENERIC_BUG_SIGNATURES and shared_sigs and parts_rel != "contradictory" and text_sim >= th.bug_possible_same_part_similarity:
            sigs_str = ", ".join(sorted(shared_sigs))
            return (
                "possible_duplicate",
                round(min(0.78, max(0.56, text_sim + 0.20)), 3),
                (f"Mesma extensão ({target.name}) com erro genérico semelhante ({sigs_str}), sem evidência específica suficiente para encerramento automático.",),
                ("generic_error_requires_review",),
                "ambiguous_same_source",
            )
        if parts_rel == "same" and text_sim >= th.bug_possible_same_part_similarity:
            return (
                "possible_duplicate",
                round(min(0.79, text_sim + 0.25), 3),
                (f"Mesma extensão ({target.name}) e mesma parte afetada com descrição semelhante.",),
                (),
                "ambiguous_same_source",
            )
        if shared_sigs and parts_rel != "contradictory":
            sigs_str = ", ".join(sorted(shared_sigs))
            return (
                "possible_duplicate",
                round(min(0.75, max(0.55, text_sim + 0.25)), 3),
                (f"Mesma extensão ({target.name}) com sintoma relacionado ({sigs_str}), porém parte ou contexto distinto.",),
                (),
                "ambiguous_same_source",
            )
        if text_sim >= th.bug_possible_text_similarity and parts_rel != "contradictory":
            return (
                "possible_duplicate",
                round(min(0.74, text_sim), 3),
                (f"Mesma extensão ({target.name}) com alta similaridade textual no relato.",),
                (),
                "ambiguous_same_source",
            )
        return None

    def _match_domain_change(self, target: ParsedIssue, cand: ParsedIssue):
        if not _same_parsed_extension_name(target, cand):
            return None
        if target.semantic_intent != INTENT_DOMAIN_CHANGE or cand.semantic_intent != INTENT_DOMAIN_CHANGE:
            return None

        t_old, t_new = target.old_url, target.new_url
        c_old, c_new = cand.old_url, cand.new_url

        if not t_new or not c_new:
            return None

        # Different validated destination domains => distinct migration (e.g. #130 vs #153)
        if t_new.is_valid_domain and c_new.is_valid_domain:
            if t_new.identity_key != c_new.identity_key:
                return None
            new_equiv, new_div = compare_source_urls(t_new, c_new)
            if not new_equiv:
                return (
                    "possible_duplicate",
                    0.66,
                    (
                        f"Mesmo domínio registrável na URL nova ({t_new.registrable_domain}), porém subdomínio, porta ou caminho de destino divergem ({new_div}: {t_new.exact_host}{t_new.normalized_path if t_new.normalized_path != '/' else ''} vs {c_new.exact_host}{c_new.normalized_path if c_new.normalized_path != '/' else ''}); requer revisão humana.",
                    ),
                    ("destination_subdomain_or_path_mismatch",),
                    "ambiguous_same_source",
                )
            same_new = True
        else:
            same_new = t_new.identity_key == c_new.identity_key

        if not same_new:
            return None

        # Distinct languages on domain-change requests cannot be auto-closed blindly
        lang_t = target.target_languages - {"all"}
        lang_c = cand.target_languages - {"all"}
        if lang_t and lang_c and not (lang_t & lang_c):
            return (
                "possible_duplicate",
                0.65,
                ("Mesma URL de destino informada, mas os idiomas solicitados diferem; requer revisão humana.",),
                ("language_scope_divergence",),
                "ambiguous_same_source",
            )

        # Both target and candidate share the EXACT same destination
        both_old_valid = bool(t_old and t_old.is_valid_domain and c_old and c_old.is_valid_domain)
        same_old_valid, old_div = (
            compare_source_urls(t_old, c_old) if both_old_valid else (False, None)
        )
        if both_old_valid and not same_old_valid:
            return (
                "possible_duplicate",
                0.64,
                (f"Mesma URL nova informada, mas URLs antigas divergem ({old_div}); requer revisão humana.",),
                ("old_url_mismatch",),
                "ambiguous_same_source",
            )

        # Degenerate domain change (old == new) cannot justify automatic closure
        if same_old_valid and t_new.is_valid_domain:
            old_vs_new, _ = compare_source_urls(t_old, t_new)
            if old_vs_new:
                return (
                    "possible_duplicate",
                    0.65,
                    ("URL antiga e URL nova idênticas no relato; requer verificação humana.",),
                    ("identical_old_and_new_url",),
                    "ambiguous_same_source",
                )
            old_label = t_old.exact_host or t_old.host  # type: ignore[union-attr]
            new_label = t_new.exact_host or t_new.host
            if cand.is_open:
                return (
                    "confirmed_duplicate",
                    0.99,
                    (
                        f"Mesma extensão ({target.name}), mesma URL antiga ({old_label}) e mesma URL nova ({new_label}).",
                    ),
                    (),
                    "duplicate",
                )
            return (
                "possible_duplicate",
                0.80,
                (f"Transição idêntica ({old_label} → {new_label}) já registrada em ticket encerrado.",),
                ("candidate_closed",),
                "historical_related",
            )

        return (
            "possible_duplicate",
            0.72,
            ("Mesma URL nova informada para a extensão, sem confirmação completa de URL antiga válida.",),
            (),
            "ambiguous_same_source",
        )

    def _match_dead_source(self, target: ParsedIssue, cand: ParsedIssue):
        if not _same_parsed_extension_name(target, cand):
            return None
        if target.semantic_intent != cand.semantic_intent:
            return None

        if _days_between(target.raw.created_at, cand.raw.created_at) > self.config.dead_source_window_days:
            return None

        # Compare domains & exact source destinations if both explicitly provide URLs
        t_url = target.url or (target.extra_urls[0] if target.extra_urls else None)
        c_url = cand.url or (cand.extra_urls[0] if cand.extra_urls else None)
        diff_dest_same_reg = False
        if t_url and c_url and t_url.is_valid_domain and c_url.is_valid_domain:
            if t_url.identity_key != c_url.identity_key:
                return None
            if not compare_source_urls(t_url, c_url)[0]:
                diff_dest_same_reg = True
        if ("vip_paywall" in target.symptom_topics) != ("vip_paywall" in cand.symptom_topics):
            diff_dest_same_reg = True

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
                    "ambiguous_same_source",
                )
            return None

        if not diff_dest_same_reg and cand.is_open and ev_t == "permanent" and ev_c == "permanent" and (
            text_sim >= th.dead_source_confirmed_text_similarity
            or (t_url and c_url and t_url.identity_key == c_url.identity_key and text_sim >= 0.35)
        ):
            return (
                "confirmed_duplicate",
                round(min(0.95, text_sim + 0.25), 3),
                (f"Mesma fonte ({target.name}) reportada como encerrada definitivamente no mesmo período.",),
                (),
                "duplicate",
            )

        if text_sim >= th.dead_source_possible_text_similarity or (ev_t == "permanent" and ev_c == "permanent"):
            return (
                "possible_duplicate",
                round(min(0.75, max(0.52, text_sim + 0.2)), 3),
                (f"Mesma fonte ({target.name}) já reportada como indisponível recentemente; confirmar se trata do mesmo evento.",),
                (),
                "ambiguous_same_source",
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

        # Semantic protection: disjoint intents, feature topics, login vs registration, or disjoint target languages
        if target.semantic_intent != cand.semantic_intent:
            return None
        feat_t = target.feature_topics - {"nsfw_adult"}
        feat_c = cand.feature_topics - {"nsfw_adult"}
        if feat_t and feat_c and not (feat_t & feat_c):
            return None
        norm_t_enh = normalize_text(f"{target.clean_title} {target.description}", preserve_urls=False)
        norm_c_enh = normalize_text(f"{cand.clean_title} {cand.description}", preserve_urls=False)
        has_login_t = bool(re.search(r"\b(?:login|logar|autentic\w*|entrar\s+na\s+conta)\b", norm_t_enh))
        has_login_c = bool(re.search(r"\b(?:login|logar|autentic\w*|entrar\s+na\s+conta)\b", norm_c_enh))
        has_reg_t = bool(re.search(r"\b(?:cadastr\w*|registr\w*|criar\s+(?:nova\s+)?conta)\b", norm_t_enh))
        has_reg_c = bool(re.search(r"\b(?:cadastr\w*|registr\w*|criar\s+(?:nova\s+)?conta)\b", norm_c_enh))
        if (has_login_t and not has_reg_t and has_reg_c and not has_login_c) or (
            has_reg_t and not has_login_t and has_login_c and not has_reg_c
        ):
            return None
        auth_scope_mismatch = (has_login_t != has_login_c) or (has_reg_t != has_reg_c)
        lang_t = target.target_languages - {"all"}
        lang_c = cand.target_languages - {"all"}
        if lang_t and lang_c and not (lang_t & lang_c):
            return None

        tok_a = self._tokens_cache.get(target.raw.number) or _combined_text_tokens(target)
        tok_b = self._tokens_cache.get(cand.raw.number) or _combined_text_tokens(cand)
        cos_sim = tfidf_cosine(tok_a, tok_b, self._idf_by_category.get(CATEGORY_ENHANCEMENT))
        jac_sim = jaccard_similarity(target.all_text_tokens, cand.all_text_tokens)
        title_sim = dice_bigrams(target.clean_title, cand.clean_title)
        combined = 0.60 * cos_sim + 0.25 * jac_sim + 0.15 * title_sim
        tok_overlap = overlap_coefficient(target.all_text_tokens, cand.all_text_tokens)
        th = self.config.thresholds

        ext_tokens = tokenize(target.related_extension or "")
        informative_shared = (target.all_text_tokens & cand.all_text_tokens) - ext_tokens - _GENERIC_BUG_STOPWORDS
        if (
            cand.is_open
            and not auth_scope_mismatch
            and feat_t == feat_c
            and len(normalize_text(target.description, preserve_urls=False)) >= 25
            and len(normalize_text(cand.description, preserve_urls=False)) >= 25
            and len(informative_shared) >= 3
            and combined >= th.enhancement_confirmed_combined_similarity
            and tok_overlap >= th.enhancement_confirmed_summary_overlap
        ):
            return (
                "confirmed_duplicate",
                round(min(0.98, combined), 3),
                ("Sugestão de melhoria funcionalmente idêntica no mesmo escopo.",),
                (),
                "duplicate",
            )

        if cand.is_open and tok_overlap >= th.enhancement_possible_token_overlap and (
            cos_sim >= th.enhancement_possible_tfidf_similarity
            or combined >= 0.48
            or bool(feat_t & feat_c)
        ):
            return (
                "possible_duplicate",
                round(min(0.78, combined + 0.12), 3),
                ("Sugestão semelhante no mesmo escopo; priorizada revisão humana antes de encerrar.",),
                (),
                "ambiguous_same_source",
            )

        if cand.is_closed and combined >= 0.65 and tok_overlap >= 0.50:
            return (
                "possible_duplicate",
                round(min(0.70, combined), 3),
                ("Sugestão semelhante a uma solicitação já encerrada.",),
                ("candidate_closed",),
                "historical_related",
            )
        return None
