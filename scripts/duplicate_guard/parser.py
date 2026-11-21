# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Structured parser for Project Nox GitHub Issue Forms (v2, v1 legacy, and headerless)."""

from __future__ import annotations

import hashlib
import re

from .models import (
    CATEGORY_BUG,
    CATEGORY_DEAD_SOURCE,
    CATEGORY_DOMAIN_CHANGE,
    CATEGORY_ENHANCEMENT,
    CATEGORY_NEW_EXTENSION,
    CATEGORY_UNKNOWN,
    CatalogExtension,
    CatalogSource,
    ExtensionCatalog,
    ParsedIssue,
    RULE_ENGINE_VERSION,
    RawIssue,
)
from .normalize import (
    classify_dead_source_evidence,
    compact_name,
    detect_extension_already_exists,
    detect_regression_signal,
    detect_work_scope_cues,
    distinctive_name_tokens,
    extract_api_endpoints,
    extract_chapter_numbers,
    extract_episode_numbers,
    extract_error_signatures,
    extract_feature_and_symptom_topics,
    extract_language_context,
    extract_specific_work_hint,
    extract_url_locale,
    extract_urls_from_text,
    has_substantive_observations_text,
    infer_semantic_intent,
    is_sparse_description,
    name_without_tld,
    normalize_language_code,
    normalize_text,
    normalize_url,
    parse_version,
    strip_accents,
    tokenize,
)

# Title prefixes used by label-issues.yml and legacy forms
_TITLE_PREFIX_RE = re.compile(
    r"^\s*(\[(?:Nova extensão|Problema na extensão|Bug|Mudança de domínio|Fonte indisponível|Fonte morta|Sugestão)\]\s*:\s*)+",
    re.IGNORECASE,
)

# Canonical field aliases across v2 templates, v1 legacy templates, and headerless issues (#145).
_FIELD_ALIASES: dict[str, str] = {
    "tipo de extensao": "type",
    "tipo": "type",
    "nome da fonte": "name",
    "nome da fonte/site": "name",
    "nome do site": "name",
    "nome da extensao": "name",
    "qual extensao esta apresentando o problema?": "name",
    "url do site": "url",
    "url oficial do site": "url",
    "url": "url",
    "url antiga": "old_url",
    "dominio ou url antigo": "old_url",
    "url nova": "new_url",
    "dominio ou url novo": "new_url",
    "versao da extensao": "extension_version",
    "versao": "extension_version",
    "aplicativo utilizado": "app_name",
    "parte afetada": "affected_part",
    "o que esta quebrado?": "broken_checklist",
    "o que esta acontecendo?": "description",
    "o que aconteceu?": "description",
    "o que aconteceu com a fonte?": "description",
    "mensagem de erro": "error_message",
    "como reproduzir?": "reproduction",
    "informacoes complementares (opcional)": "additional_info",
    "informacoes complementares": "additional_info",
    "informacoes adicionais": "additional_info",
    "detalhes adicionais": "additional_info",
    "observacoes (opcional)": "notes",
    "observacoes": "notes",
    "observacoes adicionais": "notes",
    "observacoes adicionais (opcional)": "notes",
    "descricao": "description",
    "idioma do site": "lang",
    "idiomas": "lang",
    "titulo ou resumo": "summary",
    "titulo ou resumo da sugestao": "summary",
    "descricao da melhoria": "description",
    "descricao da sugestao": "description",
    "proposta de melhoria": "description",
    "motivo": "purpose",
    "extensao relacionada (opcional)": "related_extension",
    "extensao afetada, se houver": "related_extension",
    "conteudo +18?": "nsfw",
    "o site possui conteudo +18?": "nsfw",
    "idioma": "lang",
    "precisa de login?": "login",
    "precisa de vip/premium?": "vip",
    "desde quando esta offline, se souber": "offline_since",
}

_AFFECTED_PART_MAP = {
    "popular": "catalog",
    "recentes": "catalog",
    "popular / recentes": "catalog",
    "busca": "search",
    "filtros": "search",
    "busca / filtros": "search",
    "metadados": "metadata",
    "detalhes": "metadata",
    "capitulos": "chapters",
    "capitulos/episodios": "chapters",
    "leitor": "reader",
    "imagens": "reader",
    "leitor / imagens": "reader",
    "leitor/video": "reader_or_video",
    "episodios / video": "video",
    "video": "video",
    "instalacao / atualizacao": "install",
    "login": "login",
    "outro": "other",
}


def _clean_field_key(raw_header: str) -> str:
    folded = strip_accents(raw_header or "").lower().strip()
    folded = re.sub(r"\s+", " ", folded)
    return _FIELD_ALIASES.get(folded, "")


def _clean_field_value(raw_val: str) -> str:
    val = (raw_val or "").strip()
    if not val or val.lower() == "_no response_":
        return ""
    return val


def _extract_sections(body: str) -> dict[str, str]:
    """Extract Issue Form fields from ### headers, falling back to headerless labels."""
    text = (body or "").replace("\r\n", "\n")
    if not text.strip():
        return {}

    sections: dict[str, str] = {}
    has_h3 = bool(re.search(r"^###\s+", text, flags=re.MULTILINE))

    if has_h3:
        current_key = ""
        buf: list[str] = []
        for line in text.split("\n"):
            m = re.match(r"^###\s+(.+?)\s*$", line)
            if m:
                if current_key:
                    val = _clean_field_value("\n".join(buf))
                    if val and current_key not in sections:
                        sections[current_key] = val
                current_key = _clean_field_key(m.group(1))
                buf = []
            elif current_key:
                buf.append(line)
        if current_key:
            val = _clean_field_value("\n".join(buf))
            if val and current_key not in sections:
                sections[current_key] = val
        return sections

    # Fallback for headerless form copies (real example: Issue #145)
    current_key = ""
    buf = []
    for line in text.split("\n"):
        candidate_key = _clean_field_key(line.strip())
        if candidate_key:
            if current_key:
                val = _clean_field_value("\n".join(buf))
                if val and current_key not in sections:
                    sections[current_key] = val
            current_key = candidate_key
            buf = []
        elif current_key:
            buf.append(line)
    if current_key:
        val = _clean_field_value("\n".join(buf))
        if val and current_key not in sections:
            sections[current_key] = val
    return sections


def _detect_category(raw: RawIssue, sections: dict[str, str]) -> str:
    label_set = {strip_accents(l).lower() for l in raw.labels}
    if "nova extensao" in label_set or "kind:new-extension" in label_set:
        return CATEGORY_NEW_EXTENSION
    if "problema na extensao" in label_set or "bug" in label_set or "kind:bug" in label_set:
        return CATEGORY_BUG
    if "mudanca de dominio" in label_set or "kind:domain" in label_set:
        return CATEGORY_DOMAIN_CHANGE
    if "fonte indisponivel" in label_set or "fonte morta" in label_set or "kind:dead-source" in label_set:
        return CATEGORY_DEAD_SOURCE
    if "sugestao" in label_set or "kind:enhancement" in label_set:
        return CATEGORY_ENHANCEMENT

    title_folded = strip_accents(raw.title).lower().strip()
    if title_folded.startswith("[nova extensao]"):
        return CATEGORY_NEW_EXTENSION
    if title_folded.startswith("[problema na extensao]") or title_folded.startswith("[bug]"):
        return CATEGORY_BUG
    if title_folded.startswith("[mudanca de dominio]"):
        return CATEGORY_DOMAIN_CHANGE
    if title_folded.startswith("[fonte indisponivel]") or title_folded.startswith("[fonte morta]"):
        return CATEGORY_DEAD_SOURCE
    if title_folded.startswith("[sugestao]"):
        return CATEGORY_ENHANCEMENT

    if "affected_part" in sections or "broken_checklist" in sections or "reproduction" in sections or "extension_version" in sections:
        return CATEGORY_BUG
    if "old_url" in sections or "new_url" in sections:
        return CATEGORY_DOMAIN_CHANGE
    if "summary" in sections or "purpose" in sections:
        return CATEGORY_ENHANCEMENT
    if "url" in sections and ("login" in sections or "vip" in sections or "lang" in sections):
        return CATEGORY_NEW_EXTENSION
    if "name" in sections and "description" in sections:
        return CATEGORY_DEAD_SOURCE
    return CATEGORY_UNKNOWN


def _detect_ext_type(raw: RawIssue, sections: dict[str, str]) -> str | None:
    field_type = strip_accents(sections.get("type", "")).lower()
    if "manga" in field_type:
        return "Mangá"
    if "anime" in field_type:
        return "Anime"

    label_set = {strip_accents(l).lower() for l in raw.labels}
    has_manga = "manga" in label_set or "type:manga" in label_set
    has_anime = "anime" in label_set or "type:anime" in label_set
    if has_manga and not has_anime:
        return "Mangá"
    if has_anime and not has_manga:
        return "Anime"
    return None


_FREE_TEXT_PART_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("reader", re.compile(r"\b(?:no\s+leitor|ao\s+abrir\s+(?:o\s+)?leitor|abre\s+o\s+leitor|paginas?\s+nao\s+carregam?|no\s+pages\s+found|ler\s+(?:os?\s+|algum\s+)?capitulo|imagens?\s+(?:nao\s+carregam|em\s+branco|quebradas))\b", re.I)),
    ("login", re.compile(r"\b(?:fazer\s+login|no\s+login|erro\s+(?:de|no)\s+login|autentic\w*|minha\s+conta|fazer\s+cadastro|logar\s+na\s+conta)\b", re.I)),
    ("chapters", re.compile(r"\b(?:baixar\s+capitulos?|download\s+(?:de|dos)\s+capitulos?|lista(?:gem)?\s+de\s+capitulos?|nao\s+aparece(?:m)?\s+na\s+listagem|olhar\s+(?:os\s+)?capitulos?|nenhum\s+capitulo|faltando\s+capitulos?|ao\s+baixar|/chapters?)\b", re.I)),
    ("search", re.compile(r"\b(?:na\s+busca|na\s+pesquisa|ao\s+(?:buscar|pesquisar)|pesquisa\s+nao\s+retorna|busca\s+quebrada|filtros?\s+de\s+busca|/search)\b", re.I)),
    ("catalog", re.compile(r"\b(?:no\s+catalogo|aba\s+(?:popular|recentes)|listagem\s+inicial|tela\s+inicial|populares)\b", re.I)),
    ("metadata", re.compile(r"\b(?:imagens?\s+de\s+capa|capas?\s+(?:incorretas?|erradas?|nao\s+carregam|quebradas?)|metadados?\s+(?:errad[oa]s?|incorret[oa]s?)|sinopse|informacoes\s+das\s+obras)\b", re.I)),
    ("video", re.compile(r"\b(?:reproduzir\s+(?:video|episodio)|no\s+player|video\s+nao\s+carrega|episodios?\s+(?:\d+\s+)?nao\s+reproduz|/episodes?)\b", re.I)),
    ("install", re.compile(r"\b(?:nao\s+instala|erro\s+ao\s+(?:instalar|atualizar)\s+(?:a\s+)?extensao)\b", re.I)),
)


def _infer_parts_from_free_text(free_text: str) -> set[str]:
    norm = normalize_text(free_text, preserve_urls=False)
    if not norm:
        return set()
    return {part for part, pat in _FREE_TEXT_PART_PATTERNS if pat.search(norm)}


_PARSER_INCOMPATIBLE_PARTS = (
    (frozenset({"login"}), frozenset({"reader", "video", "search", "catalog", "chapters", "metadata", "install"})),
    (frozenset({"search"}), frozenset({"reader", "video", "login", "install"})),
    (frozenset({"install"}), frozenset({"reader", "video", "search", "chapters", "metadata", "login"})),
    (frozenset({"reader"}), frozenset({"search", "login", "install"})),
    (frozenset({"video"}), frozenset({"search", "login", "install"})),
)


def _parse_affected_parts(sections: dict[str, str], ext_type: str | None, *, free_text: str = "") -> frozenset[str]:
    parts: set[str] = set()
    raw_part = strip_accents(sections.get("affected_part", "")).lower().strip()
    if raw_part:
        mapped = _AFFECTED_PART_MAP.get(raw_part)
        if mapped:
            parts.add(mapped)

    checklist = sections.get("broken_checklist", "")
    if checklist:
        for line in checklist.split("\n"):
            m = re.match(r"^\s*-\s*\[x\]\s*(.+?)\s*$", line, flags=re.IGNORECASE)
            if m:
                item = strip_accents(m.group(1)).lower().strip()
                mapped = _AFFECTED_PART_MAP.get(item)
                if mapped == "reader_or_video":
                    parts.add("video" if ext_type == "Anime" else "reader")
                elif mapped:
                    parts.add(mapped)

    if free_text:
        inferred = _infer_parts_from_free_text(free_text)
        concrete = parts - {"other"}
        if not concrete and inferred:
            parts.discard("other")
            parts.update(inferred)
        elif concrete and inferred and not (concrete & inferred):
            contradicts = any(
                (concrete <= g1 and inferred <= g2) or (concrete <= g2 and inferred <= g1)
                for g1, g2 in _PARSER_INCOMPATIBLE_PARTS
            )
            if contradicts:
                parts = set(inferred)
    return frozenset(parts)


def _detect_resolution_and_status(raw: RawIssue) -> tuple[str | None, str | None, bool]:
    label_set = {strip_accents(l).lower() for l in raw.labels}
    resolution: str | None = None
    if "concluido" in label_set or "resolution:done" in label_set:
        resolution = "done"
    elif "recusado" in label_set or "resolution:declined" in label_set:
        resolution = "declined"
    elif "duplicado" in label_set or "resolution:duplicate" in label_set or raw.state_reason == "duplicate":
        resolution = "duplicate"
    elif "cancelado" in label_set or "resolution:cancelled" in label_set:
        resolution = "cancelled"

    status: str | None = None
    if "em analise" in label_set or "status:triage" in label_set:
        status = "triage"
    elif "aceito" in label_set or "status:accepted" in label_set:
        status = "accepted"
    elif "em desenvolvimento" in label_set or "status:in-progress" in label_set:
        status = "in_progress"
    elif "aguardando informacoes" in label_set or "status:needs-info" in label_set:
        status = "needs_info"

    is_dup_marked = resolution == "duplicate" or "duplicado" in label_set or raw.state_reason == "duplicate"
    return resolution, status, is_dup_marked


def _infer_related_extension_for_enhancement(
    sections: dict[str, str],
    clean_title: str,
    summary: str,
    known_extensions: frozenset[str] = frozenset(),
) -> str:
    explicit = sections.get("related_extension", "").strip()
    if explicit:
        return explicit
    # Check whether clean_title is purely an extension name (e.g. #144 "[Sugestão]: Blackout Comics")
    # or mentions a known extension in title/summary (e.g. #151 "Hanami Heaven")
    if known_extensions:
        title_compact = compact_name(clean_title)
        summary_compact = compact_name(summary)
        for ext in sorted(known_extensions, key=len, reverse=True):
            if ext and (ext in title_compact or ext in summary_compact):
                return ext
    # Heuristic: short 1-3 word title when summary differs substantially (#144)
    if clean_title and summary and len(clean_title.split()) <= 3:
        if compact_name(clean_title) not in compact_name(summary):
            return clean_title
    # Heuristic: Portuguese prepositional phrases "do/da/na/no <Name>" in title/summary
    for candidate_src in (clean_title, summary):
        m = re.search(
            r"\b(?:na extensao|da extensao|do site|da scan|na|no|do|da)\s+([A-Z][\w.-]+(?:\s+[A-Z][\w.-]+){0,2})",
            strip_accents(candidate_src),
        )
        if m:
            cand = m.group(1).strip()
            if cand.lower() not in {"extensao", "aplicativo", "catalogo", "sistema"}:
                return cand
    return ""


def parse_issue(raw: RawIssue, known_extensions: frozenset[str] = frozenset()) -> ParsedIssue:
    """Parse a raw GitHub issue into structured Project Nox fields."""
    sections = _extract_sections(raw.body)
    category = _detect_category(raw, sections)
    ext_type = _detect_ext_type(raw, sections)
    clean_title = _TITLE_PREFIX_RE.sub("", raw.title.strip()).strip()

    # Extract name
    name = sections.get("name", "").strip()
    if not name and category in (CATEGORY_NEW_EXTENSION, CATEGORY_DOMAIN_CHANGE, CATEGORY_DEAD_SOURCE):
        name = clean_title
    elif not name and category == CATEGORY_BUG:
        # Handle legacy "[Bug]: TaimuMangas, Erro..." or natural titles without ### Name
        m_sep = re.split(r"\s*[—,-]\s*", clean_title, maxsplit=1)
        if m_sep and m_sep[0] and len(m_sep[0]) <= 40:
            name = m_sep[0].strip()
        else:
            m_nat = re.match(
                r"^(?:extens[aã]o\s+)?([A-Za-z0-9À-ÿ][A-Za-z0-9À-ÿ ._-]{1,32}?)\s+(?:n[aã]o\s+|est[aá]\s+|apresenta\b|parou\b|com\s+erro|com\s+problema|dando\b|erro\b|http\b)",
                clean_title,
                flags=re.IGNORECASE,
            )
            if m_nat:
                name = m_nat.group(1).strip()

    compact = compact_name(name) or name_without_tld(name)
    dist_tokens = distinctive_name_tokens(name)

    url_obj = normalize_url(sections.get("url", ""))
    old_url_obj = normalize_url(sections.get("old_url", ""))
    new_url_obj = normalize_url(sections.get("new_url", ""))

    description = sections.get("description", "").strip()
    error_message = sections.get("error_message", "").strip()
    if error_message:
        description = f"{description}\n{error_message}".strip()
    summary = sections.get("summary", "").strip()
    purpose = sections.get("purpose", "").strip()
    if category == CATEGORY_ENHANCEMENT:
        combined_desc = "\n".join(p for p in (summary, description, purpose) if p)
        description = combined_desc

    reproduction = sections.get("reproduction", "").strip()
    additional_info = "\n".join(
        p for p in (sections.get("additional_info", ""), sections.get("notes", ""), sections.get("offline_since", "")) if p
    ).strip()

    extra_urls = extract_urls_from_text("\n".join((description, reproduction, additional_info)))
    raw_url_field = sections.get("url", "").strip()
    if raw_url_field.startswith("/") and extra_urls:
        base_u = extra_urls[0]
        if base_u and base_u.is_valid_domain:
            port_part = f":{base_u.port}" if base_u.port else ""
            resolved_rel = f"{base_u.scheme}://{base_u.exact_host}{port_part}{raw_url_field}"
            rel_obj = normalize_url(resolved_rel)
            if rel_obj and rel_obj.is_valid_domain:
                url_obj = rel_obj
    # If new_extension had no URL field parsed directly, try extra_urls
    if category == CATEGORY_NEW_EXTENSION and url_obj is None and extra_urls:
        url_obj = extra_urls[0]

    version_raw = sections.get("extension_version", "").strip()
    version_tuple = parse_version(version_raw)
    app_name = sections.get("app_name", "").strip()

    # Remove extension name prefix from clean_title for text comparison in bugs
    title_symptom = clean_title
    if name:
        title_symptom = re.sub(rf"^{re.escape(name)}\s*[-—:,]\s*", "", title_symptom, flags=re.IGNORECASE).strip()

    free_parts_text = "\n".join(
        p for p in (title_symptom, description, reproduction, additional_info) if p
    )
    affected_parts = _parse_affected_parts(sections, ext_type, free_text=free_parts_text)

    related_ext = ""
    if category == CATEGORY_ENHANCEMENT:
        related_ext = _infer_related_extension_for_enhancement(sections, clean_title, summary, known_extensions)

    obs_text_for_new_ext = "\n".join(
        p for p in (sections.get("notes", ""), sections.get("description", ""), sections.get("additional_info", "")) if p
    )
    free_intent_title = "" if (category == CATEGORY_NEW_EXTENSION and name and compact_name(title_symptom) == compact) else title_symptom
    combined_symptom_text = "\n".join(p for p in (title_symptom, description, reproduction, additional_info) if p)
    free_intent_text = (
        obs_text_for_new_ext
        if (category == CATEGORY_NEW_EXTENSION and not free_intent_title)
        else "\n".join(
            p for p in (free_intent_title, sections.get("summary", ""), sections.get("description", ""), reproduction, additional_info) if p
        )
    )
    error_sigs, tech_tokens = extract_error_signatures(combined_symptom_text)
    if not version_tuple and category == CATEGORY_BUG:
        ver_m = re.search(
            r"\b(?:(?:na\s+)?vers(?:ao|ion)\s*:?\s*v?|v)(\d+\.\d+(?:\.\d+)+)\b",
            normalize_text(combined_symptom_text, preserve_urls=False),
            flags=re.IGNORECASE,
        )
        if ver_m:
            version_raw = ver_m.group(1)
            version_tuple = parse_version(version_raw)

    # Filter extension's own hostname from technical_tokens unless failure is an explicit DNS/NXDOMAIN error
    if "nxdomain_dns" not in error_sigs:
        filtered_tech: set[str] = set()
        for tok in tech_tokens:
            if tok.startswith("host:"):
                host_val = tok.split(":", 1)[1]
                host_compact = compact_name(host_val)
                if url_obj and url_obj.registrable_domain and (
                    host_val == url_obj.registrable_domain or host_val.endswith("." + url_obj.registrable_domain)
                ):
                    continue
                if compact and len(compact) >= 4 and compact in host_compact:
                    continue
            filtered_tech.add(tok)
        tech_tokens = frozenset(filtered_tech)

    all_text_tokens = tokenize(combined_symptom_text)

    declared_code, mentioned_langs, target_langs, existing_only_langs = extract_language_context(
        sections.get("lang", ""),
        free_intent_text,
        url_obj,
    )
    ext_already_exists = detect_extension_already_exists(free_intent_text)
    feature_topics, symptom_topics = extract_feature_and_symptom_topics(free_intent_text)
    has_reg_signal = detect_regression_signal(free_intent_text)
    specific_work_hint = extract_specific_work_hint(
        title_symptom,
        f"{description}\n{reproduction}\n{additional_info}",
        extension_name=name,
    )
    mentioned_chapters = extract_chapter_numbers(combined_symptom_text)
    mentioned_episodes = extract_episode_numbers(combined_symptom_text)
    api_endpoints = extract_api_endpoints(combined_symptom_text)
    has_global_scope, has_unnamed_specific = detect_work_scope_cues(combined_symptom_text)
    dead_ev = classify_dead_source_evidence(free_intent_text)

    semantic_intent, secondary_intents, inferred_cat, cat_mismatch = infer_semantic_intent(
        form_category=category,
        clean_title=clean_title,
        free_text=free_intent_text,
        extension_already_exists=ext_already_exists,
        error_signatures=error_sigs,
        feature_topics=feature_topics,
        symptom_topics=symptom_topics,
        target_languages=target_langs,
        existing_only_languages=existing_only_langs,
        has_old_url=bool(old_url_obj),
        has_new_url=bool(new_url_obj),
        dead_evidence=dead_ev,
    )

    notes_raw = sections.get("notes", "").strip()
    obs_only_text = "\n".join(p for p in (notes_raw, sections.get("description", ""), sections.get("additional_info", "")) if p)
    has_obs = has_substantive_observations_text(
        obs_only_text,
        extension_already_exists=ext_already_exists,
        feature_topics=feature_topics,
        symptom_topics=symptom_topics,
        existing_only_languages=existing_only_langs,
        error_signatures=error_sigs,
        category_mismatch=cat_mismatch,
    )

    if not related_ext and inferred_cat == CATEGORY_ENHANCEMENT:
        related_ext = name or _infer_related_extension_for_enhancement(sections, clean_title, summary, known_extensions)
    related_ext_compact = compact_name(related_ext)

    resolution, active_status, is_dup_marked = _detect_resolution_and_status(raw)
    is_open = raw.state == "open"
    is_closed = raw.state == "closed"

    sparse = is_sparse_description(description, f"{title_symptom} {additional_info}") and not error_sigs

    # Deterministic structured fingerprint preserving case-sensitive URL identity,
    # substantive observations, semantic intents, feature/symptom topics, and form flags
    # (excluding volatile fields like updated_at, labels, and comment counters).
    fp_parts = [
        RULE_ENGINE_VERSION,
        category,
        inferred_cat,
        str(cat_mismatch),
        semantic_intent,
        ",".join(sorted(secondary_intents)),
        str(ext_already_exists),
        str(has_obs),
        str(has_reg_signal),
        dead_ev,
        ext_type or "",
        compact,
        ",".join(dist_tokens),
        (url_obj.source_identity_key or url_obj.destination_key or url_obj.identity_key) if url_obj else "",
        (url_obj.destination_key or url_obj.identity_key) if url_obj else "",
        url_obj.norm_path if url_obj and hasattr(url_obj, "norm_path") else (url_obj.normalized_path if url_obj else ""),
        url_obj.functional_query if url_obj else "",
        (old_url_obj.source_identity_key or old_url_obj.destination_key or old_url_obj.identity_key) if old_url_obj else "",
        (old_url_obj.destination_key or old_url_obj.identity_key) if old_url_obj else "",
        (new_url_obj.source_identity_key or new_url_obj.destination_key or new_url_obj.identity_key) if new_url_obj else "",
        (new_url_obj.destination_key or new_url_obj.identity_key) if new_url_obj else "",
        ",".join(
            f"{u.destination_key or u.identity_key}:{u.normalized_path}?{u.functional_query}"
            for u in extra_urls
        ),
        version_raw,
        app_name.strip().lower(),
        declared_code,
        ",".join(sorted(mentioned_langs)),
        ",".join(sorted(target_langs)),
        ",".join(sorted(existing_only_langs)),
        ",".join(sorted(affected_parts)),
        ",".join(sorted(feature_topics)),
        ",".join(sorted(symptom_topics)),
        ",".join(sorted(error_sigs)),
        ",".join(sorted(tech_tokens)),
        specific_work_hint,
        str(has_global_scope),
        str(has_unnamed_specific),
        ",".join(sorted(mentioned_chapters)),
        ",".join(sorted(mentioned_episodes)),
        ",".join(sorted(api_endpoints)),
        notes_raw,
        normalize_text(notes_raw, preserve_urls=True),
        normalize_text(summary, preserve_urls=True),
        normalize_text(purpose, preserve_urls=True),
        normalize_text(
            f"{sections.get('login', '')}|{sections.get('vip', '')}|{sections.get('nsfw', '')}",
            preserve_urls=False,
        ),
        normalize_text(f"{title_symptom} {description} {reproduction} {additional_info}", preserve_urls=True),
        related_ext_compact,
    ]
    fp_hash = hashlib.sha256("|".join(fp_parts).encode("utf-8")).hexdigest()[:16]

    return ParsedIssue(
        raw=raw,
        category=category,
        ext_type=ext_type,
        name=name,
        compact_name=compact,
        distinctive_tokens=dist_tokens,
        clean_title=clean_title,
        url=url_obj,
        old_url=old_url_obj,
        new_url=new_url_obj,
        extra_urls=extra_urls,
        version_raw=version_raw,
        version_tuple=version_tuple,
        app_name=app_name,
        affected_parts=affected_parts,
        description=description,
        reproduction=reproduction,
        additional_info=additional_info,
        related_extension=related_ext,
        related_extension_compact=related_ext_compact,
        error_signatures=error_sigs,
        technical_tokens=tech_tokens,
        all_text_tokens=all_text_tokens,
        resolution=resolution,
        active_status=active_status,
        is_open=is_open,
        is_closed=is_closed,
        is_duplicate_marked=is_dup_marked,
        content_fingerprint=fp_hash,
        body_is_sparse=sparse,
        declared_language=declared_code,
        semantic_intent=semantic_intent,
        secondary_intents=secondary_intents,
        inferred_category=inferred_cat,
        category_mismatch=cat_mismatch,
        extension_already_exists=ext_already_exists,
        mentioned_languages=mentioned_langs,
        target_languages=target_langs,
        existing_only_languages=existing_only_langs,
        feature_topics=feature_topics,
        symptom_topics=symptom_topics,
        has_substantive_observations=has_obs,
        has_regression_signal=has_reg_signal,
        specific_work_hint=specific_work_hint,
        mentioned_chapters=mentioned_chapters,
        mentioned_episodes=mentioned_episodes,
        api_endpoints=api_endpoints,
        title_symptom=title_symptom,
        has_global_work_scope=has_global_scope,
        has_unnamed_specific_work_scope=has_unnamed_specific,
        notes=notes_raw,
    )


_CATALOG_PREFIX_RE = re.compile(r"^(?:aniyomi|tachiyomi|mihon|nox)\s*:\s*", re.IGNORECASE)


def parse_catalog_entries(raw_entries: list[dict], ext_type: str, catalog_repo: str) -> list[CatalogExtension]:
    """Parse entries from a Project Nox index.min.json catalog preserving per-source URL and language bindings."""
    parsed: list[CatalogExtension] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        raw_name = str(entry.get("name") or "").strip()
        clean_name = _CATALOG_PREFIX_RE.sub("", raw_name).strip()
        if not clean_name:
            continue
        entry_langs = normalize_language_code(str(entry.get("lang") or ""), allow_short_iso=True)
        raw_sources = [s for s in (entry.get("sources") or ()) if isinstance(s, dict)]

        domains: set[str] = set()
        base_urls: list[str] = []
        catalog_sources: list[CatalogSource] = []

        for src in raw_sources:
            raw_src_name = str(src.get("name") or "").strip()
            src_name = _CATALOG_PREFIX_RE.sub("", raw_src_name).strip() or clean_name
            src_compact = compact_name(src_name) or name_without_tld(src_name)
            src_id = str(src.get("id") or "").strip()
            src_langs = normalize_language_code(str(src.get("lang") or ""), allow_short_iso=True)

            base_contradictory = False
            if src_langs:
                # Extension package-level 'all' is a container marker for multi-source/universal APKs;
                # only flag contradiction when single-source entry declares a conflicting concrete language.
                if (
                    len(raw_sources) == 1
                    and entry_langs
                    and "all" not in entry_langs
                    and not any(l.startswith("unknown:") for l in entry_langs)
                    and "all" not in src_langs
                    and not any(l.startswith("unknown:") for l in src_langs)
                    and not (src_langs <= entry_langs)
                ):
                    base_contradictory = True
                eff_langs = src_langs
            elif len(raw_sources) == 1 and entry_langs:
                # Single-source entry without per-source lang safely inherits extension-level lang.
                eff_langs = entry_langs
            else:
                # Multi-source package where an individual source omits lang: never propagate another source's or global lang.
                eff_langs = frozenset()

            raw_base = str(src.get("baseUrl") or "").strip()
            for candidate_u in re.split(r"[\s,]+", raw_base):
                if not candidate_u:
                    continue
                u_obj = normalize_url(candidate_u)
                if u_obj and u_obj.is_valid_domain:
                    domains.add(u_obj.identity_key)
                    if candidate_u not in base_urls:
                        base_urls.append(candidate_u)
                    url_loc = extract_url_locale(u_obj)
                    url_lang_contradicts = bool(
                        url_loc
                        and eff_langs
                        and "all" not in eff_langs
                        and not any(l.startswith("unknown:") for l in eff_langs)
                        and url_loc not in eff_langs
                    )
                    catalog_sources.append(
                        CatalogSource(
                            name=src_name,
                            compact_name=src_compact,
                            source_id=src_id,
                            base_url=candidate_u,
                            url=u_obj,
                            languages=eff_langs,
                            has_contradictory_metadata=(base_contradictory or url_lang_contradicts),
                        )
                    )

        non_contradictory_langs: set[str] = set()
        for src_obj in catalog_sources:
            if not src_obj.has_contradictory_metadata:
                non_contradictory_langs.update(src_obj.languages)
        ext_contradictory = bool(catalog_sources and all(s.has_contradictory_metadata for s in catalog_sources))
        version_str = str(entry.get("version") or "").strip()
        parsed.append(
            CatalogExtension(
                ext_type=ext_type,
                name=clean_name,
                compact_name=compact_name(clean_name) or name_without_tld(clean_name),
                pkg=str(entry.get("pkg") or "").strip(),
                apk=str(entry.get("apk") or "").strip(),
                version=version_str,
                version_tuple=parse_version(version_str),
                languages=frozenset(non_contradictory_langs),
                domains=frozenset(domains),
                base_urls=tuple(base_urls),
                catalog_repo=catalog_repo,
                sources=tuple(catalog_sources),
                package_languages=entry_langs,
                has_contradictory_metadata=ext_contradictory,
            )
        )
    return parsed


def build_extension_catalog(catalog_payloads: dict[str, list[dict]] | None) -> ExtensionCatalog:
    """Build an ExtensionCatalog from {'manga': [...], 'anime': [...]} raw payloads."""
    if not isinstance(catalog_payloads, dict):
        return ExtensionCatalog()
    extensions: list[CatalogExtension] = []
    manga_rows = catalog_payloads.get("manga") or catalog_payloads.get("Mangá") or []
    anime_rows = catalog_payloads.get("anime") or catalog_payloads.get("Anime") or []
    if isinstance(manga_rows, list):
        extensions.extend(parse_catalog_entries(manga_rows, "Mangá", "ProjectNox-org/manga-extensoes"))
    if isinstance(anime_rows, list):
        extensions.extend(parse_catalog_entries(anime_rows, "Anime", "ProjectNox-org/anime-extensoes"))
    return ExtensionCatalog(extensions)
