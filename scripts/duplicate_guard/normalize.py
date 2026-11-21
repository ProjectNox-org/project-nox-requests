# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Deterministic text, name, URL, version, and similarity utilities."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from urllib.parse import urlparse

from .models import NormalizedUrl

# Multi-label public suffixes commonly used by manga/anime scanlators & portals.
MULTI_LABEL_TLDS = frozenset({
    "com.br", "org.br", "net.br", "edu.br", "gov.br",
    "co.uk", "org.uk", "ac.uk",
    "com.mx", "com.ar", "com.co", "com.pe", "com.es", "com.pt",
    "co.jp", "ne.jp", "or.jp",
    "com.au", "net.au", "com.tr", "com.ua",
})

# Hosting platforms where subdomain or first path segment defines identity.
SHARED_HOST_DOMAINS = frozenset({
    "github.io",
    "pages.dev",
    "workers.dev",
    "blogspot.com",
    "blogspot.com.br",
    "wordpress.com",
    "netlify.app",
    "vercel.app",
    "herokuapp.com",
    "web.app",
    "firebaseapp.com",
    "onrender.com",
    "t.me",
    "telegram.me",
    "discord.gg",
    "discord.com",
    "sites.google.com",
    "linktr.ee",
    "carrd.co",
})

GENERIC_NAME_TOKENS = frozenset({
    "manga", "mangas", "manhwa", "manhua", "webtoon", "webtoons",
    "anime", "animes", "hentai", "scan", "scans", "scanlator",
    "comics", "comic", "toon", "toons", "online", "hd", "tv",
    "br", "pt", "site", "web", "leitor", "extensao", "fonte",
    "sub", "dub", "oficial", "app", "net", "org", "com",
})

STOPWORDS = frozenset({
    # Portuguese
    "a", "o", "os", "as", "um", "uma", "uns", "umas", "de", "do", "da", "dos", "das",
    "em", "no", "na", "nos", "nas", "por", "para", "pra", "pro", "com", "sem", "sob",
    "e", "ou", "mas", "que", "se", "como", "quando", "onde", "porque", "pois",
    "ao", "aos", "a", "as", "pelo", "pela", "pelos", "pelas", "este", "esta", "isto",
    "esse", "essa", "isso", "aquele", "aquela", "aquilo", "meu", "minha", "seu", "sua",
    "nao", "sim", "ja", "ainda", "mais", "muito", "muita", "bem", "so", "apenas",
    "esta", "estao", "estar", "fica", "ficando", "ser", "foi", "era", "tem", "ter",
    "tendo", "faz", "fazer", "vai", "vou", "aqui", "ali", "la", "tudo", "nada",
    "algum", "alguma", "algumas", "alguns", "qualquer", "todas", "todos", "todo", "toda",
    "entre", "sobre", "apos", "antes", "depois", "durante", "ate", "mesmo", "mesma",
    "extensao", "aplicativo", "versao", "tipo", "obra", "obras", "favor", "gostaria",
    # English
    "the", "and", "or", "for", "with", "from", "that", "this", "is", "are", "was",
    "were", "not", "no", "yes", "to", "in", "on", "at", "of", "by", "an", "be",
    # Template noise
    "no response", "optional", "opcional", "exemplo", "ex", "http", "https", "www",
})

IGNORED_ATTACHMENT_HOSTS = frozenset({
    "github.com",
    "raw.githubusercontent.com",
    "user-images.githubusercontent.com",
    "private-user-images.githubusercontent.com",
    "objects.githubusercontent.com",
})

GENERIC_INSUFFICIENT_PHRASES = frozenset({
    "nao funciona", "nao abre", "nao pega", "nao carrega", "quebrou",
    "parou de funcionar", "esta com erro", "deu erro", "erro", "bug",
    "extensao quebrada", "extensao com problema", "ajuda", "arruma",
    "arrumar", "consertar", "nao esta funcionando", "parou",
})

_DOMAIN_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_URL_CANDIDATE_RE = re.compile(
    r"(?:https?|htpps|htpp)://[^\s<>()\"'\]]+|(?:https?|htpps|htpp)//:[^\s<>()\"'\]]+|\b(?:[a-z0-9-]+\.)+[a-z]{2,15}(?:/[^\s<>()\"'\]]*)?",
    re.IGNORECASE,
)
_IMG_OR_HTML_RE = re.compile(r"<img\b[^>]*>|<[^>]+>", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")


def strip_accents(value: str) -> str:
    """Remove Unicode diacritical marks using NFKD decomposition."""
    nfkd = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def normalize_text(value: str, preserve_urls: bool = True) -> str:
    """Normalize text to lowercase ASCII-folded form while keeping error terms."""
    text = str(value or "")
    text = _IMG_OR_HTML_RE.sub(" ", text)
    if preserve_urls:
        text = _MD_LINK_RE.sub(r" \1 \2 ", text)
    else:
        text = _MD_LINK_RE.sub(r" \1 ", text)
    text = strip_accents(text).lower()
    # Normalize common scheme typos inside text too
    text = re.sub(r"\bhtpps?://", "https://", text)
    text = re.sub(r"\b(https?)//:", r"\1://", text)
    # Remove punctuation noise except '.', '/', ':', '-', '_' inside tokens
    text = re.sub(r"[^\w\s./:+\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def compact_name(name: str) -> str:
    """Collapse a source/extension name into lowercase alphanumeric characters."""
    folded = strip_accents(str(name or "")).lower()
    # Strip trailing domain suffixes only if space-separated? Keep them in compact_name
    # because MangaLivre.lat vs MangaLivre should still be compared thoughtfully.
    return re.sub(r"[^a-z0-9]+", "", folded)


def name_without_tld(name: str) -> str:
    """Return compact name stripping common trailing domain extensions like .lat/.com."""
    folded = strip_accents(str(name or "")).lower().strip()
    stripped = re.sub(
        r"\.(?:com\.br|org\.br|net\.br|com|net|org|lat|xyz|to|tv|cc|io|in|co|cfd|network|beer|gay|world|site|website|app|dev|online|club|vip|info|pro|top|me)$",
        "",
        folded,
    )
    return re.sub(r"[^a-z0-9]+", "", stripped)


def distinctive_name_tokens(name: str) -> tuple[str, ...]:
    """Extract non-generic lowercase tokens from an extension/source name."""
    folded = strip_accents(str(name or "")).lower()
    raw_tokens = re.findall(r"[a-z0-9]+", folded)
    filtered = [t for t in raw_tokens if t not in GENERIC_NAME_TOKENS and len(t) >= 2]
    return tuple(filtered) if filtered else tuple(raw_tokens)


def _compute_registrable_domain(host: str) -> str:
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    last_two = ".".join(parts[-2:])
    if last_two in MULTI_LABEL_TLDS and len(parts) >= 3:
        return ".".join(parts[-3:])
    return last_two


def normalize_url(raw_value: str) -> NormalizedUrl | None:
    """Safely parse and normalize a URL or domain string without executing/fetching it."""
    raw = str(raw_value or "").strip()
    if not raw or raw.lower() == "_no response_":
        return None

    # Strip surrounding angle brackets or markdown punctuation
    cleaned = raw.strip("<>\"'` ").rstrip(".,;)")
    if not cleaned:
        return None

    lowered = cleaned.lower()
    if re.match(r"^(javascript|data|vbscript|file):", lowered):
        return None

    # Fix common user typos observed in real issues (#1 https//: , #103 htpps://)
    cleaned = re.sub(r"^htpps://", "https://", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^htpp://", "http://", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(https?)//:", r"\1://", cleaned, flags=re.IGNORECASE)

    had_scheme = bool(re.match(r"^[a-z][a-z0-9+.-]*://", cleaned, flags=re.IGNORECASE))
    candidate = cleaned if had_scheme else f"https://{cleaned}"

    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None

    if parsed.scheme.lower() not in ("http", "https"):
        return None

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    # Strip www. / www2. / m. prefix only when there are still >= 2 labels left
    stripped_host = re.sub(r"^(?:www\d*|m)\.", "", host)
    if "." in stripped_host:
        host = stripped_host

    is_valid = bool(host and _DOMAIN_PATTERN.match(host))
    # Reject obvious non-TLDs like "pt.redetoons" or version-like "1.6.21"
    if is_valid:
        tld = host.rsplit(".", 1)[-1]
        if tld.isdigit() or len(tld) < 2 or tld in ("redetoons", "cineveo"):
            is_valid = False

    if not is_valid:
        ident = compact_name(cleaned)
        if not ident:
            return None
        return NormalizedUrl(
            raw=raw,
            scheme=parsed.scheme.lower() if had_scheme else "",
            host="",
            registrable_domain="",
            host_stem="",
            normalized_path="",
            identity_key=f"id:{ident}",
            is_valid_domain=False,
            is_shared_host=False,
            raw_identifier=cleaned.lower(),
        )

    reg_domain = _compute_registrable_domain(host)
    is_shared = (reg_domain in SHARED_HOST_DOMAINS) or (host in SHARED_HOST_DOMAINS)
    host_stem = reg_domain.split(".", 1)[0]

    # Normalize path (strip trailing slashes; preserve meaningful segments)
    path = re.sub(r"/+", "/", parsed.path or "/").strip("/")
    norm_path = f"/{path}" if path else "/"

    # Build canonical identity key:
    # - For shared hosting (github.io, t.me, blogspot, workers.dev), keep subdomain + 1st path segment if community host.
    if is_shared:
        if host in ("t.me", "telegram.me", "discord.gg", "discord.com", "linktr.ee", "sites.google.com"):
            first_seg = path.split("/", 1)[0].lower() if path else ""
            identity_key = f"shared:{host}/{first_seg}".rstrip("/")
        else:
            identity_key = f"shared:{host}"
    else:
        identity_key = f"domain:{reg_domain}"

    return NormalizedUrl(
        raw=raw,
        scheme=parsed.scheme.lower(),
        host=host,
        registrable_domain=reg_domain,
        host_stem=host_stem,
        normalized_path=norm_path,
        identity_key=identity_key,
        is_valid_domain=True,
        is_shared_host=is_shared,
        raw_identifier=cleaned.lower(),
    )


def extract_urls_from_text(text: str) -> tuple[NormalizedUrl, ...]:
    """Extract normalized non-attachment URLs/domains mentioned in free text."""
    if not text:
        return ()
    # Remove GitHub user-attachments URLs before matching
    cleaned = _IMG_OR_HTML_RE.sub(" ", text)
    found: list[NormalizedUrl] = []
    seen_keys: set[str] = set()
    for m in _URL_CANDIDATE_RE.finditer(cleaned):
        raw_tok = m.group(0)
        # Skip common Java class/method strings like java.lang.Object or e.g.
        if re.match(r"^(?:java|android|kotlin|kotlinx|okhttp3|org\.json)\.", raw_tok, re.IGNORECASE):
            continue
        if re.search(r"\.(?:png|jpg|jpeg|webp|gif|svg|txt|patch|json|log|apk)$", raw_tok, re.IGNORECASE):
            continue
        parsed = normalize_url(raw_tok)
        if not parsed or not parsed.is_valid_domain:
            continue
        if parsed.host in IGNORED_ATTACHMENT_HOSTS and ("user-attachments" in raw_tok or "assets" in raw_tok):
            continue
        if parsed.host in IGNORED_ATTACHMENT_HOSTS:
            continue
        if parsed.identity_key not in seen_keys:
            seen_keys.add(parsed.identity_key)
            found.append(parsed)
    return tuple(found)


def parse_version(raw: str) -> tuple[int, ...]:
    """Parse version strings like '1.6.21', 'v14.5', '14,5' into comparable int tuples."""
    cleaned = str(raw or "").strip().replace(",", ".")
    if not cleaned:
        return ()
    m = re.search(r"v?(\d+(?:\.\d+)+)", cleaned, re.IGNORECASE)
    if m:
        return tuple(int(p) for p in m.group(1).split("."))
    m_single = re.search(r"\b(\d+)\b", cleaned)
    if m_single:
        return (int(m_single.group(1)),)
    return ()


def tokenize(text: str) -> frozenset[str]:
    """Tokenize normalized text excluding short noise and common stopwords."""
    norm = normalize_text(text, preserve_urls=False)
    tokens = re.findall(r"[a-z0-9]+", norm)
    return frozenset(
        t for t in tokens
        if len(t) >= 2 and t not in STOPWORDS
    )


def tokenize_list(text: str) -> list[str]:
    """Return ordered list of informative tokens (preserving duplicates for TF-IDF)."""
    norm = normalize_text(text, preserve_urls=False)
    tokens = re.findall(r"[a-z0-9]+", norm)
    return [t for t in tokens if len(t) >= 2 and t not in STOPWORDS]


def jaccard_similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Compute Jaccard similarity between two token sets."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def overlap_coefficient(a: frozenset[str], b: frozenset[str]) -> float:
    """Compute Szymkiewicz-Simpson overlap coefficient |A ∩ B| / min(|A|, |B|)."""
    if not a or not b:
        return 0.0
    denom = min(len(a), len(b))
    return len(a & b) / denom if denom else 0.0


def dice_bigrams(a: str, b: str) -> float:
    """Compute Sorensen-Dice similarity over character bigrams."""
    sa = re.sub(r"\s+", " ", strip_accents(a or "").lower().strip())
    sb = re.sub(r"\s+", " ", strip_accents(b or "").lower().strip())
    if not sa or not sb:
        return 0.0
    if sa == sb:
        return 1.0
    if len(sa) < 2 or len(sb) < 2:
        return 1.0 if sa == sb else 0.0
    bg_a = Counter(sa[i : i + 2] for i in range(len(sa) - 1))
    bg_b = Counter(sb[i : i + 2] for i in range(len(sb) - 1))
    inter = sum((bg_a & bg_b).values())
    total = sum(bg_a.values()) + sum(bg_b.values())
    return (2.0 * inter / total) if total else 0.0


def levenshtein_distance(a: str, b: str) -> int:
    """Compute Damerau-Levenshtein-like optimal string alignment distance."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    # Optimal string alignment handles adjacent character transpositions (e.g. wacht vs watch)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(
                d[i - 1][j] + 1,
                d[i][j - 1] + 1,
                d[i - 1][j - 1] + cost,
            )
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


def levenshtein_ratio(a: str, b: str) -> float:
    """Return normalized string similarity in [0.0, 1.0] using optimal string alignment."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    max_len = max(len(a), len(b))
    dist = levenshtein_distance(a, b)
    return max(0.0, 1.0 - (dist / max_len))


def tfidf_cosine(tokens_a: list[str], tokens_b: list[str], corpus_idf: dict[str, float] | None = None) -> float:
    """Compute TF-IDF cosine similarity between two documents."""
    if not tokens_a or not tokens_b:
        return 0.0
    tf_a = Counter(tokens_a)
    tf_b = Counter(tokens_b)
    vocab = set(tf_a) | set(tf_b)

    def idf(tok: str) -> float:
        if corpus_idf and tok in corpus_idf:
            return corpus_idf[tok]
        # Fallback smooth IDF on 2-doc pair
        df = (1 if tok in tf_a else 0) + (1 if tok in tf_b else 0)
        return math.log((2 + 1) / (df + 1)) + 1.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for tok in vocab:
        w = idf(tok)
        va = tf_a.get(tok, 0) * w
        vb = tf_b.get(tok, 0) * w
        dot += va * vb
        norm_a += va * va
        norm_b += vb * vb
    denom = math.sqrt(norm_a) * math.sqrt(norm_b)
    return (dot / denom) if denom > 0.0 else 0.0


def build_corpus_idf(doc_token_lists: list[list[str]]) -> dict[str, float]:
    """Compute smooth IDF weights over the issue corpus."""
    n = len(doc_token_lists)
    if n == 0:
        return {}
    df: Counter[str] = Counter()
    for toks in doc_token_lists:
        df.update(set(toks))
    return {tok: math.log((n + 1) / (freq + 1)) + 1.0 for tok, freq in df.items()}


_ERROR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("no_pages_found", re.compile(r"\bno\s+pages\s+found\b|\bnenhuma\s+pagina\s+encontrada\b|\bsem\s+paginas\b", re.I)),
    ("no_results_found", re.compile(r"\bno\s+results\s+found\b|\bnenhum\s+resultado\s+encontrado\b|\bno\s+manga\s+found\b", re.I)),
    ("cloudflare", re.compile(r"\bcloudflare\b|\bcontornar\s+a?\s*cloudflare\b|\bturnstile\b|\bchallenge\b", re.I)),
    ("nxdomain_dns", re.compile(r"\bnxdomain\b|\bunknownhostexception\b|\bnao\s+foi\s+possivel\s+resolver\b|\bresolve\s+host\b", re.I)),
    ("json_decode", re.compile(r"\bjsondecodingexception\b|\bunexpected\s+json\s+token\b|\bjson\s+input\b|\bsyntaxerror.*json\b", re.I)),
    ("null_pointer", re.compile(r"\bnullpointerexception\b|\bnull\s+object\s+reference\b", re.I)),
    ("ssl_tls", re.compile(r"\bsslhandshakeexception\b|\bcertpathvalidatorexception\b|\bssl\b|\bcertificado\b", re.I)),
    ("timeout", re.compile(r"\bsockettimeoutexception\b|\btimed?\s*out\b|\btempo\s+esgotado\b", re.I)),
    ("login_auth", re.compile(r"\blogin\b|\bautenticac[aã]o\b|\bcadastro\b|\bsess[aã]o\b|\bconta\b.*\bentrar\b", re.I)),
    ("slow_download", re.compile(r"\blentid[aã]o\b|\bdemorando\b|\bdemora\b.*\bbaixar\b|\bdownload.*\blento\b", re.I)),
    ("missing_chapters", re.compile(r"\bnao\s+(?:esta|estao)\s+aparecendo\s+todos\s+os\s+capitulos\b|\bso\s+mostra\s+\d+\s+capitulos\b|\bfaltando\s+capitulos\b", re.I)),
    ("video_playback", re.compile(r"\bnao\s+reproduz\b|\bvideo\s+nao\s+carrega\b|\berro\s+ao\s+reproduzir\b|\bplayer\b", re.I)),
    ("site_blocked", re.compile(r"\bsite\s+bloqueou\b|\bbloqueou\s+o\s+acesso\b|\bbloqueio\b", re.I)),
)

_HTTP_CODE_RE = re.compile(r"\b(?:http|erro|status|code)?\s*[:=]?\s*(400|401|403|404|410|429|500|502|503|504|520|521|522|523|525|526)\b", re.I)
_TECH_HOST_RE = re.compile(r"\b([a-z0-9-]+\.(?:[a-z0-9-]+\.)+[a-z]{2,10})\b", re.I)


def extract_error_signatures(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """Extract canonical error signatures and technical host/exception tokens."""
    norm = normalize_text(text, preserve_urls=True)
    sigs: set[str] = set()
    tech: set[str] = set()

    for sig_name, pattern in _ERROR_PATTERNS:
        if pattern.search(norm):
            sigs.add(sig_name)

    for m in _HTTP_CODE_RE.finditer(norm):
        # Avoid false HTTP code matches on extension version substrings like 1.4.403
        start = m.start()
        prefix = norm[max(0, start - 6) : start]
        if re.search(r"\d\.\s*$", prefix):
            continue
        code = m.group(1)
        sigs.add(f"http:{code}")
        tech.add(f"http:{code}")

    for m in _TECH_HOST_RE.finditer(norm):
        host = m.group(1).lower()
        if not host.startswith(("java.", "android.", "kotlin.", "github.com", "user-images.")):
            tech.add(f"host:{host}")

    for exc_m in re.finditer(r"\b([a-z]+(?:exception|error))\b", norm):
        tech.add(f"exc:{exc_m.group(1)}")

    return frozenset(sigs), frozenset(tech)


def classify_dead_source_evidence(text: str) -> str:
    """Classify dead-source evidence as 'permanent', 'temporary', or 'unclear'."""
    norm = normalize_text(text, preserve_urls=False)
    permanent_hits = sum(
        1 for pat in (
            r"\bencerramento\s+definitivo\b", r"\bencerrou\b", r"\bencerrad[ao]\b",
            r"\bdescontinuad[ao]\b", r"\bexpirou\b", r"\bdominio\s+expirado\b",
            r"\bnxdomain\b", r"\bsuspendedpage\b", r"\bsuspended\b",
            r"\bdiscord\b.*\bconfirm", r"\banuncio\s+oficial\b", r"\bconfirmaram\b",
            r"\bfechou\b", r"\bfaliu\b", r"\bnao\s+existe\s+mais\b", r"\bsemanas?\b", r"\bmeses?\b",
        )
        if re.search(pat, norm)
    )
    temporary_hits = sum(
        1 for pat in (
            r"\bmanutencao\b", r"\binstabilidade\b", r"\btemporari[ao]\b",
            r"\bvoltou\b", r"\bhoje\b", r"\bagora\b", r"\bcaiu\s+hoje\b",
            r"\bcloudflare\b", r"\b502\b", r"\b503\b", r"\b52[0-6]\b",
        )
        if re.search(pat, norm)
    )
    if permanent_hits >= 1 and temporary_hits == 0:
        return "permanent"
    if temporary_hits >= 1 and permanent_hits == 0:
        return "temporary"
    return "unclear"


def is_sparse_description(description: str, extra: str = "") -> bool:
    """Return True if bug description is empty or lacks actionable detail."""
    combined = normalize_text(f"{description} {extra}", preserve_urls=False)
    if not combined or len(combined) < 15:
        return True
    if combined in GENERIC_INSUFFICIENT_PHRASES:
        return True
    return len(tokenize(combined)) < 2


def sanitize_for_markdown(value: str, max_len: int = 120) -> str:
    """Sanitize untrusted issue text before rendering into a GitHub Markdown comment."""
    text = str(value or "")
    # Remove HTML comments, tags, and control characters
    text = text.replace("<!--", "").replace("-->", "")
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"[\r\n\t\x00-\x1f\x7f]+", " ", text)
    # Neutralize @mentions so bots never ping unintended users
    text = re.sub(r"@([A-Za-z0-9_-]+)", "@\u200b\\1", text)
    # Neutralize markdown links/images and heading characters
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = text.replace("`", "'").replace("[", "(").replace("]", ")")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text
