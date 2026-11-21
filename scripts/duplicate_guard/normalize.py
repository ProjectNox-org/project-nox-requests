# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Deterministic text, name, URL, version, and similarity utilities."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlsplit

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


_HOMEPAGE_NAV_SEGMENTS = frozenset({
    "home", "index", "index.html", "index.php", "inicio", "main", "principal", "ranking-do-site",
})


def _compute_scope_path(norm_path: str) -> str:
    """Fold strictly benign homepage navigation segments while preserving case-sensitive resource paths."""
    stripped = (norm_path or "/").strip("/")
    if not stripped or stripped.lower() in _HOMEPAGE_NAV_SEGMENTS:
        return "/"
    parts = stripped.split("/")
    if len(parts) == 2 and _map_locale_label(parts[0]) and parts[1].lower() in _HOMEPAGE_NAV_SEGMENTS:
        return f"/{parts[0]}"
    return norm_path


def _normalize_functional_query(raw_query: str) -> str:
    """Keep sorted non-volatile, non-secret query parameters preserving functional key/value case."""
    if not raw_query:
        return ""
    kept: list[tuple[str, str]] = []
    for k, v in parse_qsl(raw_query, keep_blank_values=True):
        kl = strip_accents(k or "").strip().lower()
        if not kl or kl in _SENSITIVE_QUERY_KEYS or kl in _VOLATILE_QUERY_KEYS or kl.startswith("utm_"):
            continue
        k_clean = (k or "").strip()
        v_clean = "<redacted>" if _looks_like_secret_value(v) else (v or "").strip()
        kept.append((k_clean, v_clean))
    if not kept:
        return ""
    kept.sort()
    return urlencode(kept, safe="/")


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

    # Never store URL authority credentials in NormalizedUrl
    cleaned = re.sub(r"^(https?://)[^/@\s]+@", r"\1", cleaned, flags=re.IGNORECASE)
    had_scheme = bool(re.match(r"^[a-z][a-z0-9+.-]*://", cleaned, flags=re.IGNORECASE))
    if not had_scheme:
        cleaned = re.sub(r"^[^/\s@:]+:[^/\s@]+@", "", cleaned)
    candidate = cleaned if had_scheme else f"https://{cleaned}"

    try:
        parsed = urlparse(candidate)
        raw_port = parsed.port
    except ValueError:
        return None

    if parsed.scheme.lower() not in ("http", "https"):
        return None

    raw_host = (parsed.hostname or "").strip().lower().rstrip(".")
    norm_port = raw_port if raw_port not in (None, 80, 443) else None

    # Strip ONLY www. for exact destination identity; keep www2., m., pt., en., api., v1., etc.
    exact_host = raw_host
    stripped_www = re.sub(r"^www\.", "", raw_host)
    if "." in stripped_www:
        exact_host = stripped_www

    # Strip www. / www2. / m. prefix for general site grouping
    host = raw_host
    stripped_host = re.sub(r"^(?:www\d*|m)\.", "", raw_host)
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
            destination_key=f"id:{ident}",
            exact_host="",
            subdomain="",
            port=None,
            scope_path="",
            functional_query="",
            source_identity_key=f"id:{ident}",
        )

    reg_domain = _compute_registrable_domain(exact_host)
    subdomain = (
        exact_host[: -(len(reg_domain) + 1)]
        if exact_host.endswith("." + reg_domain) and exact_host != reg_domain
        else ""
    )
    is_shared = (reg_domain in SHARED_HOST_DOMAINS) or (exact_host in SHARED_HOST_DOMAINS) or (host in SHARED_HOST_DOMAINS)
    host_stem = reg_domain.split(".", 1)[0]

    # Decode percent-encoding, normalize repeated slashes, preserve path case, and keep functional query params
    raw_path = unquote(parsed.path or "/")
    raw_path = re.sub(r"[\x00-\x1f\x7f]+", "", raw_path)
    path = re.sub(r"/+", "/", raw_path).strip("/")
    norm_path = f"/{path}" if path else "/"
    scope_path = _compute_scope_path(norm_path)
    func_query = _normalize_functional_query(parsed.query or "")
    dest_path = norm_path if norm_path != "/" else ""
    scope_part = scope_path if scope_path != "/" else ""
    query_part = f"?{func_query}" if func_query else ""
    port_part = f":{norm_port}" if norm_port else ""
    destination_key = f"dest:{exact_host}{port_part}{dest_path}{query_part}"
    source_identity_key = f"src:{exact_host}{port_part}{scope_part}{query_part}"

    # Build canonical general site identity key (candidate discovery only):
    if is_shared:
        if host in ("t.me", "telegram.me", "discord.gg", "discord.com", "linktr.ee", "sites.google.com"):
            first_seg = path.split("/", 1)[0].lower() if path else ""
            identity_key = f"shared:{host}/{first_seg}".rstrip("/")
        else:
            identity_key = f"shared:{exact_host}"
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
        destination_key=destination_key,
        exact_host=exact_host,
        subdomain=subdomain,
        port=norm_port,
        scope_path=scope_path,
        functional_query=func_query,
        source_identity_key=source_identity_key,
    )


def compare_source_urls(
    url_a: NormalizedUrl | None,
    url_b: NormalizedUrl | None,
    *,
    ext_type_a: str | None = None,
    ext_type_b: str | None = None,
    allow_default_type_landing: bool = False,
) -> tuple[bool, str | None]:
    """Compare two normalized URLs strictly using exact host, port, case-sensitive scope path, and functional query."""
    if not url_a or not url_b or not url_a.is_valid_domain or not url_b.is_valid_domain:
        return False, "missing_or_invalid_url"
    if url_a.identity_key != url_b.identity_key:
        return False, "different_domains"
    if url_a.exact_host != url_b.exact_host:
        return False, "subdomain_divergence"
    if url_a.port != url_b.port:
        return False, "port_divergence"
    if url_a.functional_query != url_b.functional_query:
        return False, "query_divergence"
    if url_a.scope_path != url_b.scope_path:
        if (
            allow_default_type_landing
            and ext_type_a == ext_type_b == "Mangá"
            and {url_a.scope_path, url_b.scope_path} in ({"/", "/manga"}, {"/", "/mangas"})
            and "manga" in url_a.host_stem
        ):
            return True, None
        if url_a.scope_path.lower() == url_b.scope_path.lower():
            return False, "path_case_divergence"
        return False, "path_divergence"
    return True, None


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
        dedup_key = parsed.source_identity_key or parsed.destination_key or parsed.identity_key
        if dedup_key not in seen_keys:
            seen_keys.add(dedup_key)
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
    ("json_decode", re.compile(r"\bjsondecodingexception\b|\bserializationexception\b|\bunexpected\s+json\s+token\b|\bjson\s+input\b|\bsyntaxerror.*json\b|\bjson\s+invalido\b|\binvalid\s+json\b|\berro\s+(?:de|no)\s+json\b|\bfalha\s+ao\s+ler\s+json\b", re.I)),
    ("null_pointer", re.compile(r"\bnullpointerexception\b|\bnull\s+object\s+reference\b|\battempt\s+to\s+invoke\s+virtual\s+method\b", re.I)),
    ("ssl_tls", re.compile(r"\bsslhandshakeexception\b|\bcertpathvalidatorexception\b|\bssl\b|\bcertificado\b", re.I)),
    ("timeout", re.compile(r"\bsockettimeoutexception\b|\btimed?\s*out\b|\btempo\s+esgotado\b", re.I)),
    ("login_auth", re.compile(r"\blogin\b|\bautenticac[aã]o\b|\bautenticar\b|\bcadastro\b|\bsess[aã]o\b|\bconta\b.*\bentrar\b", re.I)),
    ("slow_download", re.compile(r"\blentid[aã]o\b|\bdemorando\b|\bdemora\b.*\bbaixar\b|\bdownload.*\blento\b", re.I)),
    ("missing_chapters", re.compile(r"\b(?:nao\s+(?:esta|estao)\s+aparecendo\s+todos\s+os\s+capitulos\b|\bso\s+mostra\s+\d+\s+capitulos\b|\bfaltando\s+capitulos\b|\bcapitulo[s]?\s*(?:\d+\s+)?nao\s+aparece(?:m)?(?:\s+na\s+listagem|\s+na\s+lista)?\b)", re.I)),
    ("video_playback", re.compile(r"\b(?:nao\s+reproduz(?:iu|ia|em|iram)?\b|\bvideo\s+nao\s+carrega\b|\berro\s+ao\s+reproduzir\b|\bplayer\b)", re.I)),
    ("site_blocked", re.compile(r"\bsite\s+bloqueou\b|\bbloqueou\s+o\s+acesso\b|\bbloqueio\b", re.I)),
)

_GENERIC_EXCEPTION_NAMES = frozenset({
    "exception",
    "runtimeexception",
    "ioexception",
    "httpexception",
    "sockettimeoutexception",
    "connectexception",
    "error",
    "networkerror",
    "executionexception",
    "cancellationexception",
    "illegalstateexception",
    "illegalargumentexception",
    "unknownerror",
})

_HTTP_CODE_RE = re.compile(r"\b(?:http|erro|status|code)?\s*[:=]?\s*(400|401|403|404|410|429|500|502|503|504|520|521|522|523|525|526)\b", re.I)
_TECH_HOST_RE = re.compile(r"\b([a-z0-9-]+\.(?:[a-z0-9-]+\.)+[a-z]{2,10})\b", re.I)


def extract_error_signatures(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """Extract canonical error signatures and discriminative technical tokens."""
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

    for m in _TECH_HOST_RE.finditer(norm):
        host = m.group(1).lower()
        if not host.startswith(("java.", "android.", "kotlin.", "kotlinx.", "github.com", "user-images.")):
            tech.add(f"host:{host}")

    for exc_m in re.finditer(r"\b([a-z]*(?:exception|error))\b", norm):
        exc_name = exc_m.group(1).lower()
        if exc_name in _GENERIC_EXCEPTION_NAMES:
            tech.add(f"gexc:{exc_name}")
        else:
            tech.add(f"exc:{exc_name}")

    return frozenset(sigs), frozenset(tech)


def classify_dead_source_evidence(text: str) -> str:
    """Classify dead-source evidence as 'permanent', 'temporary', or 'unclear'."""
    norm = normalize_text(text, preserve_urls=False)
    # Neutralize explicit negations like "nao fechou" / "nao encerrou" before scanning permanent shutdown cues
    scrubbed = re.sub(
        r"\bnao\s+(?:fechou|encerrou|acabou|morreu|faliu|expirou)\b",
        " ",
        norm,
    )
    permanent_hits = sum(
        1 for pat in (
            r"\bencerramento\s+definitivo\b", r"\bencerrou\b", r"\bencerrad[ao]\b",
            r"\bdescontinuad[ao]\b", r"\bexpirou\b", r"\bdominio\s+expirado\b",
            r"\bnxdomain\b", r"\bsuspendedpage\b", r"\bsuspended\b",
            r"\bdiscord\b.*\bconfirm", r"\banuncio\s+oficial\b", r"\bconfirmaram\b",
            r"\bfechou\b", r"\bfaliu\b", r"\bnao\s+existe\s+mais\b",
            r"\b(?:offline|fora\s+do\s+ar|indisponivel|inativo|caiu|sem\s+funcionar)\s+ha\s+(?:\d+\s+|varias\s+|algumas\s+)?(?:semanas?|meses?)\b",
        )
        if re.search(pat, scrubbed)
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
    # Redact embedded URL credentials and sensitive query secrets
    text = re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?i)\b(token|api_key|apikey|access_token|refresh_token|secret|password|passwd|pwd|auth|authorization|session_id|sig|signature|jwt|bearer)=([^&\s)\]]+)",
        r"\1=[REDACTED]",
        text,
    )
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


_LANG_ALIASES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pt", re.compile(r"\b(?:portugues(?:\s*\(?\s*brasil\s*\)?)?|pt-?br|pt)\b", re.I)),
    ("en", re.compile(r"\b(?:ingles(?:es)?|english|en-?us|en-?gb)\b", re.I)),
    ("es", re.compile(r"\b(?:espanhol|espanol|spanish|es-?la|es)\b", re.I)),
    ("ru", re.compile(r"\b(?:russo|russian|ru)\b", re.I)),
    ("ja", re.compile(r"\b(?:japones|japanese|jp|ja)\b", re.I)),
    ("ko", re.compile(r"\b(?:coreano|korean|ko)\b", re.I)),
    ("zh", re.compile(r"\b(?:chines|chinese|zh)\b", re.I)),
    ("fr", re.compile(r"\b(?:frances|french|fr)\b", re.I)),
    ("it", re.compile(r"\b(?:italiano|italian|it)\b", re.I)),
    ("de", re.compile(r"\b(?:alemao|german|de)\b", re.I)),
    ("ar", re.compile(r"\b(?:arabe|arabic|ar)\b", re.I)),
    ("th", re.compile(r"\b(?:tailandes|thai|th)\b", re.I)),
    ("vi", re.compile(r"\b(?:vietnamita|vietnamese|vi)\b", re.I)),
    ("id", re.compile(r"\b(?:indonesio|indonesian|bahasa\s+indonesia|id)\b", re.I)),
    ("pl", re.compile(r"\b(?:polones|polish|pl)\b", re.I)),
    ("tr", re.compile(r"\b(?:turco|turkish|tr)\b", re.I)),
    ("he", re.compile(r"\b(?:hebraico|hebrew|he|iw)\b", re.I)),
)

_LANG_TOKEN_WORD_RE = (
    r"(?:portugues(?:\s*\(?\s*brasil\s*\)?)?|pt-?br|ingles(?:es)?|english|en-?us|"
    r"espanhol|espanol|spanish|russo|russian|japones|japanese|coreano|korean|"
    r"chines|chinese|frances|french|italiano|italian|alemao|german|"
    r"arabe|arabic|tailandes|thai|vietnamita|vietnamese|indonesio|indonesian|"
    r"polones|polish|turco|turkish|hebraico|hebrew)"
)

_EXISTING_ONLY_LANG_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"\b(?:extens[aõ]es?|extensao|repositorio|focad[ao]s?|apenas|so|somente)\s+(?:esta[o]?\s+)?(?:em|para|no)?\s*(?:conteudo\s+em\s+|idioma\s+)?({_LANG_TOKEN_WORD_RE})\b", re.I),
    re.compile(rf"\bapenas\s+(?:o\s+)?(?:conteudo\s+)?(?:em|de)\s+({_LANG_TOKEN_WORD_RE})\b", re.I),
)

_DESIRED_LANG_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"\b(?:nao\s+(?:exibe|mostra|tem|possui|suporta|carrega|tras|traz)|sem|falta(?:ndo)?|suporte\s+(?:a|ao|para)?|adicionar(?:\s+suporte)?|incluir|acessar)\s+(?:o\s+|a\s+|os\s+|as\s+)?(?:conteudo|catalogo|legendas?|obras?|manga[s]?|anime[s]?|idioma)?\s*(?:em|para|de)?\s*({_LANG_TOKEN_WORD_RE})\b", re.I),
    re.compile(rf"\b(?:tambem|preciso|quero)\b[^.]{{0,35}}\b(?:em|para)\s+({_LANG_TOKEN_WORD_RE})\b", re.I),
)

_ALREADY_EXISTS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bextens(?:ao|oes)\b[^.]{0,55}\b(?:ja\s+existe|ja\s+tem|no\s+(?:teu\s+|seu\s+)?repositorio|esta\s+no\s+repositorio|vai\s+encontrar)\b", re.I),
    re.compile(r"\bja\s+existe\s+(?:a\s+extensao|no\s+(?:teu\s+|seu\s+)?repositorio|essa\s+extensao|uma\s+extensao|no\s+catalogo)\b", re.I),
    re.compile(r"\b(?:na|da|a)\s+extensao\s+(?:atual|existente|ja\s+existente|que\s+ja\s+existe)\b", re.I),
    re.compile(r"\b(?:extensao|fonte)\s+ja\s+existe\b", re.I),
    re.compile(r"\b(?:corrigir|consertar|atualizar|melhorar|modificar|adicionar\s+suporte\s+na)\s+(?:a\s+)?extensao\b", re.I),
    re.compile(r"\b(?:focad[ao]\s+apenas|nao\s+exibe\s+o\s+conteudo|nao\s+mostra\s+o\s+conteudo)\b", re.I),
)

_FEATURE_TOPIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("lang_locale", re.compile(r"\b(?:idioma[s]?|lingua[s]?|linguagem|traducao|legendado|dublado|suporte\s+(?:a|ao|para)?\s*(?:portugues|pt-?br|ingles|english|espanhol|russo|japones)|nao\s+exibe\s+o\s+conteudo\s+em)\b", re.I)),
    ("login_account", re.compile(r"\b(?:fazer\s+login|sistema\s+de\s+(?:login|cadastro)|autenticac|cookies?\s+de\s+sessao|sincronizar\s+conta|suporte\s+(?:a|ao|de)\s+(?:login|cadastro|conta)|login\s+e\s+cadastro|cadastro\s+(?:e\s+login|de\s+contas?))\b", re.I)),
    ("search_filter", re.compile(r"\b(?:filtro[s]?|filtrar|pesquisa\s+avancada|busca\s+avancada|ordenar|ordenacao|por\s+(?:genero[s]?|ano\s+de\s+lancamento|tags?))\b", re.I)),
    ("catalog_section", re.compile(r"\b(?:organiza\s+os\s+conteudos|aba\s+de\s+(?:populares|recentes|em\s+alta)|secao\s+(?:novos|populares|recentes))\b", re.I)),
    ("nsfw_adult", re.compile(r"\b(?:ocultar\s+nsfw|filtro\s+\+?18|blur\s+nsfw|modo\s+seguro)\b", re.I)),
    ("download_export", re.compile(r"\b(?:download\s+em\s+lote|baixar\s+todos|exportar\s+historico|backup\s+em\s+json|exportar\s+para\s+(?:cbz|pdf|json))\b", re.I)),
    ("tracker_sync", re.compile(r"\b(?:anilist|myanimelist|kitsu|mangaupdates|rastreamento|sincronizar\s+progresso)\b", re.I)),
    ("video_player_quality", re.compile(r"\b(?:qualidade\s+(?:do\s+)?(?:video|episodio|player|reproducao)|resolucao\s+(?:do\s+)?video|1080p|720p|480p|servidor\s+alternativo|player\s+alternativo|escolher\s+servidor)\b", re.I)),
    ("reader_layout", re.compile(r"\b(?:modo\s+leitura|pagina\s+dupla|leitura\s+webtoon|corte\s+de\s+imagem)\b", re.I)),
    ("cloudflare_webview", re.compile(r"\b(?:contornar\s+a?\s*cloudflare|bypass\s+cloudflare|custom\s+dns|doh)\b", re.I)),
)

_SYMPTOM_TOPIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("wrong_chapters", re.compile(r"\b(?:capitulos?\s+errados?|obra\s+errada|manga\s+errado|anime\s+errado|capitulos?\s+trocados?|conteudos?\s+trocados?|misturados?|de\s+outra\s+obra)\b", re.I)),
    ("chapter_order", re.compile(r"\b(?:fora\s+de\s+ordem|ordem\s+inversa|ordem\s+dos\s+capitulos|numeracao\s+errada|capitulos?\s+duplicados?)\b", re.I)),
    ("missing_content", re.compile(r"\b(?:nao\s+(?:esta|estao)\s+aparecendo\s+todos|faltando\s+(?:capitulos?|episodios?|paginas?|obras?)|nao\s+exibe\s+(?:o\s+)?conteudo|so\s+mostra\s+\d+|ausente[s]?)\b", re.I)),
    ("no_pages_or_empty", re.compile(r"\b(?:no\s+pages\s+found|nenhuma\s+pagina|sem\s+paginas|paginas?\s+nao\s+carregam?|paginas?\s+brancas?|imagens?\s+nao\s+carregam?|imagens?\s+quebradas?)\b", re.I)),
    ("audio_subtitle", re.compile(r"\b(?:legenda[s]?|dublado|audio|dessincronizad[ao]|sem\s+audio|sem\s+legenda)\b", re.I)),
    ("vip_paywall", re.compile(r"\b(?:paywall|moedas?|coins?|exige\s+(?:vip|premium|assinatura)|area\s+vip|somente\s+(?:vip|premium|pago)|vip\s+(?:bloquead|erro|pago)|premium\s+(?:bloquead|erro|pago))\b", re.I)),
    ("infinite_loading", re.compile(r"\b(?:carregamento\s+infinito|carregando\s+infinitamente|loop\s+infinito|nunca\s+carrega|nao\s+sai\s+do\s+carregando)\b", re.I)),
    ("site_redesign", re.compile(r"\b(?:reformulou|mudou\s+(?:o\s+)?site|mudou\s+(?:a\s+)?interface|mudou\s+(?:o\s+)?layout|novo\s+layout|nova\s+interface|estrutura\s+do\s+site)\b", re.I)),
    ("slow_download", re.compile(r"\b(?:lentidao|muito\s+lento|demora(?:ndo)?\s+(?:muito\s+)?(?:para|pra)?\s*baixar|download\s+lento)\b", re.I)),
    ("download_fail", re.compile(r"\b(?:ao\s+baixar\s+(?:capitulos?|episodios?)|somente\s+ao\s+baixar|erro\s+(?:ao|no)\s+(?:baixar|download)|nao\s+baixa\s+(?:capitulos?|episodios?))\b", re.I)),
    ("auth_failure", re.compile(r"\b(?:erro\s+(?:de|no)\s+login|fazer\s+login|no\s+login|nao\s+consigo\s+(?:fazer\s+)?login|autentic\w*|sessao\s+expirada|senha\s+invalida)\b", re.I)),
    ("cloudflare_block", re.compile(r"\b(?:cloudflare|turnstile|challenge|bloqueio|bloqueou\s+o\s+acesso)\b", re.I)),
    ("cover_thumbnail", re.compile(r"\b(?:imagens?\s+de\s+capa|capas?\s+(?:incorretas?|erradas?|nao\s+carregam|quebradas?)|thumbnails?|miniaturas?)\b", re.I)),
    ("profile_avatar", re.compile(r"\b(?:imagens?\s+de\s+perfil|foto\s+de\s+perfil|avatar)\b", re.I)),
    ("metadata_text", re.compile(r"\b(?:metadados?\s+(?:errad[oa]s?|incorret[oa]s?)|sinopse\s+errada|informacoes?\s+das\s+obras)\b", re.I)),
)

_REGRESSION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:voltou\s+a\s+(?:dar|ocorrer|acontecer|falhar|quebrar|apresentar)|de\s+novo|novamente)\b", re.I),
    re.compile(r"\b(?:apos\s+(?:a\s+ultima\s+)?atualiza(?:cao|r)|depois\s+da\s+atualizacao|na\s+nova\s+versao|antes\s+funcionava|parou\s+novamente)\b", re.I),
    re.compile(r"\bregressao\b", re.I),
)

_NON_SUBSTANTIVE_NOTE_RE = re.compile(
    r"^(?:site\s+muito\s+(?:bom|otimo|legal|rapido)|por\s+favor\s+adicionem|adicionem\s+por\s+favor|obrigad[ao]|valeu|top|pfv|pls|n/a|nenhuma?|nada)\b",
    re.I,
)


_EMPTY_LANG_PLACEHOLDERS = frozenset({
    "no response",
    "na",
    "n a",
    "none",
    "nenhum",
    "nenhuma",
    "nada",
    "nao informado",
    "unspecified",
})


def normalize_language_code(raw_lang: str, *, allow_short_iso: bool = False) -> frozenset[str]:
    """Normalize a declared or extracted language string to ISO-like lowercase codes."""
    text = normalize_text(raw_lang, preserve_urls=False)
    cleaned_check = re.sub(r"[^a-z0-9]+", " ", text).strip()
    if not cleaned_check or cleaned_check in _EMPTY_LANG_PLACEHOLDERS:
        return frozenset()
    if re.search(r"\b(?:all|multi(?:lingue|idioma)?|varios|todas?|todos)\b", text):
        return frozenset({"all"})
    codes: set[str] = set()
    for code, pat in _LANG_ALIASES:
        if pat.search(text):
            codes.add(code)
    if allow_short_iso:
        # Handle bare catalog ISO codes such as 'en', 'pt-br', 'es-419', 'vi', 'th', etc.
        primary = re.split(r"[-_\s/]", text.strip())[0]
        if primary == "jp":
            primary = "ja"
        if re.fullmatch(r"[a-z]{2,3}", primary) and primary not in ("no", "nd"):
            codes.add(primary)
    if not codes:
        slug = compact_name(cleaned_check)[:24]
        if slug:
            return frozenset({f"unknown:{slug}"})
    return frozenset(codes)


def _map_locale_label(label: str) -> str:
    tok = (label or "").strip().lower()
    if tok in ("pt", "pt-br", "br"):
        return "pt"
    if tok in ("en", "en-us", "en-gb"):
        return "en"
    if tok in ("es", "es-la"):
        return "es"
    if tok in ("ru", "ja", "jp", "ko", "zh", "fr", "it", "de"):
        return "ja" if tok == "jp" else tok
    return ""


def extract_url_locale(url: NormalizedUrl | None) -> str:
    """Extract an explicit leading path locale ('/br/', '/pt/') or subdomain locale ('pt.', 'en.')."""
    if not url or not url.is_valid_domain:
        return ""
    path = (url.normalized_path or "").strip("/").lower()
    if path:
        mapped = _map_locale_label(path.split("/", 1)[0])
        if mapped:
            return mapped
    if url.subdomain:
        for seg in url.subdomain.split("."):
            mapped = _map_locale_label(seg)
            if mapped:
                return mapped
    return ""


def extract_language_context(
    declared_lang: str,
    text: str,
    url: NormalizedUrl | None = None,
) -> tuple[str, frozenset[str], frozenset[str], frozenset[str]]:
    """Extract (declared_code, mentioned_languages, target_languages, existing_only_languages)."""
    declared_codes = normalize_language_code(declared_lang, allow_short_iso=True)
    declared_code = next(iter(sorted(declared_codes)), "")

    norm_text = normalize_text(text, preserve_urls=False)
    mentioned: set[str] = set()
    for code, pat in _LANG_ALIASES:
        if pat.search(norm_text):
            mentioned.add(code)

    raw_existing_only: set[str] = set()
    for pat in _EXISTING_ONLY_LANG_PATTERNS:
        for m in pat.finditer(norm_text):
            raw_existing_only.update(normalize_language_code(m.group(1)))

    raw_desired: set[str] = set()
    for pat in _DESIRED_LANG_PATTERNS:
        for m in pat.finditer(norm_text):
            raw_desired.update(normalize_language_code(m.group(1)))

    has_existing_ext_context = bool(
        detect_extension_already_exists(norm_text)
        or re.search(r"\b(?:focad[ao]s?|extens(?:ao|oes)|repositorio|no\s+catalogo)\b", norm_text)
        or (raw_desired - raw_existing_only)
    )
    existing_only: set[str] = set(raw_existing_only) if has_existing_ext_context else set()
    desired: set[str] = set(raw_desired) if has_existing_ext_context else (raw_desired | raw_existing_only)
    desired -= existing_only

    url_loc = extract_url_locale(url)
    if url_loc:
        mentioned.add(url_loc)

    target: set[str] = set(desired)
    if not target:
        if declared_codes:
            target.update(declared_codes)
        elif url_loc:
            target.add(url_loc)
    elif declared_codes and not (declared_codes & existing_only):
        target.update(declared_codes)

    mentioned.update(target)
    mentioned.update(existing_only)
    return declared_code, frozenset(mentioned), frozenset(target), frozenset(existing_only)


def detect_extension_already_exists(text: str) -> bool:
    """Return True when free-form text indicates the extension already exists in Project Nox."""
    norm = normalize_text(text, preserve_urls=False)
    if not norm:
        return False
    return any(pat.search(norm) for pat in _ALREADY_EXISTS_PATTERNS)


def extract_feature_and_symptom_topics(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """Extract normalized requested feature topics and concrete failure symptom topics."""
    norm = normalize_text(text, preserve_urls=False)
    if not norm:
        return frozenset(), frozenset()
    features = frozenset(name for name, pat in _FEATURE_TOPIC_PATTERNS if pat.search(norm))
    symptoms = frozenset(name for name, pat in _SYMPTOM_TOPIC_PATTERNS if pat.search(norm))
    return features, symptoms


def detect_regression_signal(text: str) -> bool:
    """Return True when report explicitly describes recurrence/post-update regression."""
    norm = normalize_text(text, preserve_urls=False)
    if not norm:
        return False
    return any(pat.search(norm) for pat in _REGRESSION_PATTERNS)


_GENERIC_WORK_HINTS = frozenset({
    "qualquer",
    "alguma",
    "algum",
    "todas",
    "todos",
    "extensao",
    "fonte",
    "site",
    "leitor",
    "aplicativo",
    "catalogo",
    "lista",
    "listagem",
    "biblioteca",
    "especifica",
    "especifico",
    "erro",
    "problema",
})

_CHAPTER_COUNT_NOISE_RE = re.compile(
    r"\b(?:mostra|aparecem?|apenas|so|somente|exibe)\s+\d+\s+capitulos\b",
    re.IGNORECASE,
)


_NON_WORK_TITLE_TOKENS = frozenset({
    "erro", "erros", "error", "errors", "problema", "problemas", "bug", "bugs",
    "falha", "falhas", "leitor", "capitulo", "capitulos", "pagina", "paginas",
    "imagem", "imagens", "capa", "capas", "sinopse", "titulo", "titulos",
    "serie", "series", "historia", "historias", "novel", "novels", "hq", "livro",
    "especifica", "especifico", "especificas", "especificos", "exclusivo", "exclusiva",
    "video", "videos", "player", "busca", "pesquisa", "filtro", "filtros",
    "login", "conta", "autenticacao", "cadastro", "catalogo", "popular", "populares",
    "recentes", "historico", "favoritos", "biblioteca", "download", "downloads",
    "lentidao", "lento", "atualizacao", "instalacao", "metadados", "detalhes",
    "fonte", "extensao", "extensoes", "site", "app", "aplicativo", "api",
    "endpoint", "endpoints", "rota", "http", "https", "json", "cloudflare",
    "timeout", "ssl", "dns", "nxdomain", "mihon", "aniyomi", "komikku",
    "suwayomi", "tadami", "reikai", "kotatsu", "tachiyomi", "todas", "todos",
    "qualquer", "nenhum", "nenhuma", "sem", "nao", "carrega", "abre", "funciona",
    "reproduz", "pages", "found", "unknown", "resolver", "host", "avatar",
    "perfil", "episodio", "episodios", "ep", "cap", "lista", "listagem",
    "buffer", "audio", "legenda", "legendas", "vip", "paywall", "get", "post",
    "portugues", "ingles", "espanhol", "japones", "english", "spanish",
    "webview", "android", "windows", "linux", "github", "discord", "chrome",
    "obra", "obras", "geral", "ocorre", "ocorrem", "acontece", "aparece",
})

_NON_WORK_LEADING_TOKENS = frozenset({
    "ao", "quando", "para", "pra", "com", "em", "na", "no", "da", "de", "do",
    "dos", "das", "um", "uma", "o", "a", "os", "as", "e", "ou", "que", "se", "por",
    "erro", "falha", "problema", "bug", "nao", "sem", "extensao", "site",
    "app", "http", "https", "json", "endpoint", "rota", "player", "leitor",
    "busca", "pesquisa", "login", "cadastro", "capitulo", "capitulos", "cap",
    "episodio", "episodios", "ep", "pagina", "paginas", "todas", "todos",
    "alguma", "algum", "qualquer", "nenhum", "nenhuma", "tentar", "abrir", "ler",
    "baixar", "entrar", "selecionar", "clicar", "acessar", "ir", "assistir",
    "serie", "series", "titulo", "titulos", "historia", "historias", "obra", "obras",
    "especifica", "especifico", "exclusivo", "exclusiva", "determinado", "determinada",
})

_NON_WORK_NAVIGATION_WORDS = frozenset({
    "tentar", "clicar", "selecionar", "entrar", "abrir", "baixar", "assistir",
    "qualquer", "nenhum", "nenhuma", "todas", "todos", "alguma", "algum",
    "especifico", "especifica", "especificos", "especificas",
})

_GLOBAL_WORK_SCOPE_RE = re.compile(
    r"\b(?:todas?\s+as?\s+(?:obras?|mangas?|animes?|series?|historias?|titulos?)|"
    r"todos?\s+os?\s+(?:mangas?|animes?|capitulos?|episodios?|titulos?)|"
    r"qualquer\s+(?:obra|manga|anime|serie|titulo|historia|capitulo|episodio)|"
    r"em\s+geral|no\s+catalogo\s+inteiro|na\s+extensao\s+toda)\b",
    re.IGNORECASE,
)

_UNNAMED_SPECIFIC_SCOPE_RE = re.compile(
    r"\b(?:(?:um|uma|algum|alguma|determinad[oa]|cert[oa])\s+(?:obra|manga|anime|serie|titulo|historia|capitulo|episodio)\s+especific[oa]|"
    r"(?:obras?|capitulos?|episodios?)\s+especific[oa]s?|"
    r"especific[oa]\s+(?:de\s+)?(?:uma|alguma)\s+obra)\b",
    re.IGNORECASE,
)


def detect_work_scope_cues(text: str) -> tuple[bool, bool]:
    """Detect explicit global-scope ('todas as obras', 'qualquer capítulo') vs unnamed specific-scope cues."""
    norm = normalize_text(text, preserve_urls=False)
    if not norm:
        return False, False
    return bool(_GLOBAL_WORK_SCOPE_RE.search(norm)), bool(_UNNAMED_SPECIFIC_SCOPE_RE.search(norm))


def _is_valid_work_candidate(cand: str, ext_compact: str) -> str:
    norm = re.sub(r"\s+", " ", re.sub(r"[^\w\s'-]", " ", strip_accents(cand or "").lower())).strip()
    if not norm:
        return ""
    toks = norm.split()
    if not (1 <= len(toks) <= 6):
        return ""
    if toks[0] in _NON_WORK_LEADING_TOKENS or toks[0].isdigit():
        return ""
    if any(t in _NON_WORK_NAVIGATION_WORDS for t in toks):
        return ""
    if len(toks) == 1 and (toks[0].endswith(("exception", "error")) or toks[0] in _NON_WORK_TITLE_TOKENS or len(toks[0]) < 2):
        return ""
    if all((t in _NON_WORK_TITLE_TOKENS or t in _GENERIC_WORK_HINTS or t.isdigit()) for t in toks):
        return ""
    if ext_compact and compact_name(norm) == ext_compact:
        return ""
    if len(compact_name(norm)) < 2:
        return ""
    return norm


def _normalize_multi_work_candidate(raw_cand: str, ext_compact: str) -> str:
    """Split 'Naruto e Bleach' into a sorted multi-work identifier 'bleach + naruto' if both halves are valid works."""
    stripped = strip_accents(raw_cand or "").strip()
    if re.search(r"\s+(?:e|and)\s+", stripped, flags=re.IGNORECASE):
        parts = re.split(r"\s+(?:e|and)\s+", stripped, flags=re.IGNORECASE)
        if len(parts) >= 2:
            valid_parts = [_is_valid_work_candidate(p, ext_compact) for p in parts]
            if all(valid_parts) and len(set(valid_parts)) >= 2:
                return " + ".join(sorted(set(valid_parts)))
    return _is_valid_work_candidate(stripped, ext_compact)


def extract_specific_work_hint(title_symptom: str, description: str, extension_name: str = "") -> str:
    """Extract specific manga/anime/series title hint(s) when issue scopes the problem to concrete work(s)."""
    work_nouns = r"(?:obra|manga|anime|manhwa|manhua|webtoon|serie|titulo|historia|novel|light\s+novel|comic|hq)"
    conj_stop = r"e\s+(?:abrir|clicar|selecionar|retornando|tentar|ler|assistir|carregar|entrar|acessar|ir|nao|sem|com|quando|ao|no|na)\b"
    stop_after = (
        rf"(?=\s*,\s*|\s+(?:verbda|no|na|ao|quando|esta|estao|nao|aparece|acontece|ocorre|ocorrem|"
        rf"apresenta|retorna|falha|carrega|com|dando|da|de|do|em|para|capitulos?|episodios?|{conj_stop})\b|$)"
    )
    patterns = (
        re.compile(
            rf"\b(?:apenas|so|somente)\s+(?:na|no|em)\s+{work_nouns}\s+"
            rf"([a-z0-9][a-z0-9\s'-]{{1,34}}?)(?=\s*,\s*|\s+(?:da|de|do|dos|das|verbda|esta|estao|nao|capitulo|capitulos|episodio|episodios|erro|aparece|acontece|ocorre|ocorrem|apresenta|retorna|falha|carrega|no|na|para|ao|com|{conj_stop})\b|$)"
        ),
        re.compile(
            rf"(?:/(?:chapters?|episodes?)/|\b(?:capitulos?|cap\.?|episodios?|ep\.?)\s*#?)\d+(?:\.\d+)?\s+(?:de|da|do)\s+(?:{work_nouns}\s+)?"
            rf"([a-z0-9][a-z0-9\s'-]{{1,34}}?){stop_after}"
        ),
        re.compile(
            rf"\b(?:na|da|do|de|no|pela|pelo|em)\s+{work_nouns}\s+([a-z0-9][a-z0-9\s'-]{{1,34}}?)"
            rf"(?=\s*,\s*|\s+(?:verbda|no|na|ao|quando|esta|estao|nao|aparece|acontece|ocorre|ocorrem|apresenta|retorna|falha|carrega|com|dando|da|de|do|em|para|retornando|abrir|clicar|selecionar|capitulos?|episodios?|{conj_stop})\b|$)"
        ),
        re.compile(
            rf"\b(?:problema|erro|falha|bug)\s+exclusiv[oa]\s+(?:de|da|do|em|na|no)\s+(?:{work_nouns}\s+)?"
            rf"([a-z0-9][a-z0-9\s'-]{{1,34}}?){stop_after}"
        ),
        re.compile(
            rf"\b(?:ocorre|acontece|aparece|falha)?\s*(?:somente|apenas|so|exclusivamente)\s+(?:em|no|na)\s+(?:{work_nouns}\s+)?"
            rf"([a-z0-9][a-z0-9\s'-]{{1,34}}?){stop_after}"
        ),
        re.compile(
            rf"\b(?:ao|quando\s+tento|tentando)\s+(?:abrir|ler|assistir|carregar)\s+(?:o\s+|a\s+)?(?:{work_nouns}\s+)?"
            rf"([a-z0-9][a-z0-9\s'-]{{1,34}}?){stop_after}"
        ),
        re.compile(
            r"\b(?:manga|anime|serie|obra)\s+([a-z0-9][a-z0-9-]{2,28}(?:\s+[a-z0-9-]{2,18}){0,2})\s+"
            r"(?:capitulos?\s+errados?|nao\s+carrega|trocad[oa])"
        ),
    )
    ext_compact = compact_name(extension_name)
    chunks: list[str] = []
    for src in (title_symptom or "", description or ""):
        for piece in re.split(r"[\r\n]+|(?<=[.!?;])\s+|(?<=[a-z0-9])->|\s->\s", src):
            piece_s = piece.strip()
            if piece_s:
                chunks.append(piece_s)
    if not chunks:
        chunks.append(f"{title_symptom} {description}")

    collected_hints: list[str] = []
    for chunk in chunks:
        raw_marked = re.sub(r"\bdá\b", " verbda ", chunk, flags=re.IGNORECASE)
        # Keep clause-level comma delimiter outside numbers/decimals
        comma_kept = re.sub(r",\s+", " , ", raw_marked)
        comma_kept = re.sub(r"[.;:!?]+(?=\s|$)", " ", comma_kept)
        combined = normalize_text(comma_kept.replace(" , ", "__COMMA__"), preserve_urls=False)
        combined = re.sub(r"\s+", " ", combined.replace("__comma__", " , ")).strip()
        for pat in patterns:
            m = pat.search(combined)
            if m:
                hint = _normalize_multi_work_candidate(m.group(1), ext_compact)
                if hint and hint not in collected_hints:
                    collected_hints.append(hint)
                    break
        else:
            # Multi-work capitalized mention like "em Naruto e Bleach"
            m_multi_cap = re.search(
                r"\b(?:em|no|na)\s+([A-ZÀ-Ý][A-Za-zÀ-ÿ0-9'-]{2,20}(?:\s+[A-ZÀ-Ý0-9][A-Za-zÀ-ÿ0-9'-]{1,20}){0,2})\s+e\s+([A-ZÀ-Ý][A-Za-zÀ-ÿ0-9'-]{2,20}(?:\s+[A-ZÀ-Ý0-9][A-Za-zÀ-ÿ0-9'-]{1,20}){0,2})\b",
                chunk.strip(),
            )
            if m_multi_cap:
                w1 = _is_valid_work_candidate(m_multi_cap.group(1), ext_compact)
                w2 = _is_valid_work_candidate(m_multi_cap.group(2), ext_compact)
                if w1 and w2:
                    combo = " + ".join(sorted({w1, w2}))
                    if combo not in collected_hints:
                        collected_hints.append(combo)
                    continue
            # Capitalized work at clause end ("... em Naruto.")
            m_cap_em = re.search(
                r"\bem\s+([A-ZÀ-Ý][A-Za-zÀ-ÿ0-9'-]{2,20}(?:\s+(?:[A-ZÀ-Ý0-9][A-Za-zÀ-ÿ0-9'-]{1,20}|of|the|no|de|da)){0,3})\s*[.,;:!?]*$",
                chunk.strip(),
            )
            if m_cap_em:
                hint = _is_valid_work_candidate(m_cap_em.group(1), ext_compact)
                if hint and hint not in collected_hints:
                    collected_hints.append(hint)

    if collected_hints:
        return " + ".join(sorted(set(collected_hints))) if len(set(collected_hints)) > 1 else collected_hints[0]

    # Also extract work references placed directly in the title ("Naruto não carrega", "One Piece — No pages found", "Naruto e Bleach")
    clean_ts = strip_accents(title_symptom or "").strip()
    if clean_ts:
        parts_ts = re.split(r"\s+(?:—|–|-|:)\s+", clean_ts, maxsplit=1)
        if len(parts_ts) == 2:
            left = _normalize_multi_work_candidate(parts_ts[0], ext_compact)
            if left:
                return left
        norm_ts = normalize_text(re.sub(r"\bdá\b", " verbda ", title_symptom or "", flags=re.IGNORECASE), preserve_urls=False)
        m_title = re.match(
            r"^([a-z0-9][a-z0-9\s'-]{1,34}?)\s+(?:nao\s+(?:carrega|carregam|abre|abrem|funciona|funcionam|reproduz|reproduzem|aparece|aparecem)|esta\s+sem\s+(?:paginas?|capitulos?)|sem\s+(?:paginas?|capitulos?)|no\s+pages\s+found|com\s+(?:erro|falha|problema)|capitulos?\s+errados?|fora\s+de\s+ordem)\b",
            norm_ts,
        )
        if m_title:
            cand_w = _normalize_multi_work_candidate(m_title.group(1), ext_compact)
            if cand_w:
                return cand_w
    return ""


def extract_chapter_numbers(text: str) -> frozenset[str]:
    """Extract explicit chapter identifiers (ignoring quantitative counts like 'só mostra 5 capítulos')."""
    norm = normalize_text(text, preserve_urls=True)
    if not norm:
        return frozenset()
    scrubbed = _CHAPTER_COUNT_NOISE_RE.sub(" ", norm)
    nums: set[str] = set()
    for pat in (
        re.compile(r"\b(?:capitulo|cap\.?)\s*#?(\d+(?:\.\d+)?)\b", re.IGNORECASE),
        re.compile(r"/chapters?/(\d+(?:\.\d+)?)(?:\b|/|\?|$)", re.IGNORECASE),
    ):
        for m in pat.finditer(scrubbed):
            nums.add(m.group(1))
    return frozenset(nums)


def extract_episode_numbers(text: str) -> frozenset[str]:
    """Extract explicit episode identifiers from free text or API paths."""
    norm = normalize_text(text, preserve_urls=True)
    if not norm:
        return frozenset()
    nums: set[str] = set()
    for pat in (
        re.compile(r"\b(?:episodio|ep\.?)\s*#?(\d+(?:\.\d+)?)\b", re.IGNORECASE),
        re.compile(r"/episodes?/(\d+(?:\.\d+)?)(?:\b|/|\?|$)", re.IGNORECASE),
    ):
        for m in pat.finditer(norm):
            nums.add(m.group(1))
    return frozenset(nums)


_API_PATH_PREFIX_RE = re.compile(
    r"^/(?:api|v\d+(?:\.\d+)?|graphql|gql|rest|rpc|ajax|wp-json|chapters?|episodes?|search|details?|auth|login|token|stream|manifest|hls|maintenance)(?:/|$)",
    re.IGNORECASE,
)
_STATIC_OR_ASSET_SUFFIX_RE = re.compile(
    r"\.(?:png|jpg|jpeg|webp|gif|svg|ico|css|js|woff2?|ttf|apk|zip|txt|log)$",
    re.IGNORECASE,
)
_SENSITIVE_QUERY_KEYS = frozenset({
    "token", "api_key", "apikey", "access_token", "refresh_token",
    "auth", "authorization", "key", "secret", "password", "passwd", "pwd",
    "session", "session_id", "sid", "sig", "signature", "jwt", "bearer", "cookie",
})
_VOLATILE_QUERY_KEYS = frozenset({
    "_", "_t", "t", "ts", "timestamp", "cachebuster", "cb", "_cb",
    "nocache", "rand", "nonce", "utm_source", "utm_medium", "utm_campaign",
    "utm_term", "utm_content", "fbclid", "gclid",
})


def _looks_like_secret_value(val: str) -> bool:
    s = (val or "").strip()
    if re.match(r"^eyJ[A-Za-z0-9_-]{8,}", s):
        return True
    if len(s) >= 24 and re.fullmatch(r"[A-Za-z0-9_.-]+", s) and any(c.isdigit() for c in s) and any(c.isalpha() for c in s):
        return True
    return False


def _canonicalize_endpoint(raw_path_and_query: str) -> str | None:
    cleaned = unquote(str(raw_path_and_query or "").strip("<>\"'`.,;!?) "))
    if not cleaned:
        return None
    if re.match(r"^(?:java|android|kotlin|kotlinx|okhttp3|retrofit2|org\.json)\.", cleaned, re.IGNORECASE):
        return None
    try:
        sp = urlsplit(cleaned if cleaned.startswith("/") else f"/{cleaned.lstrip('/')}")
    except ValueError:
        return None
    raw_path = re.sub(r"/+", "/", sp.path or "/").strip("/")
    if not raw_path:
        return None
    if _STATIC_OR_ASSET_SUFFIX_RE.search(raw_path):
        return None
    norm_segs: list[str] = []
    for seg in raw_path.split("/"):
        if _looks_like_secret_value(seg):
            norm_segs.append("<redacted>")
        else:
            norm_segs.append(seg.lower())
    canon_path = "/" + "/".join(norm_segs)
    if len(canon_path) < 3:
        return None

    kept_params: list[tuple[str, str]] = []
    if sp.query:
        for k, v in parse_qsl(sp.query, keep_blank_values=True):
            k_clean = k.strip()
            kl = k_clean.lower()
            if not kl or kl in _SENSITIVE_QUERY_KEYS or kl in _VOLATILE_QUERY_KEYS or kl.startswith("utm_"):
                continue
            vl = "<redacted>" if _looks_like_secret_value(v) else v.strip()
            kept_params.append((k_clean, vl))
    if kept_params:
        kept_params.sort()
        return f"{canon_path}?{urlencode(kept_params, safe='/')}"
    return canon_path


def extract_api_endpoints(text: str) -> frozenset[str]:
    """Extract normalized technical HTTP routes / API endpoints (including full URLs) without leaking secrets."""
    if not text:
        return frozenset()
    pre = _IMG_OR_HTML_RE.sub(" ", str(text))
    pre = _MD_LINK_RE.sub(r" \1 \2 ", pre)
    pre = pre.replace("`", " ")
    # Normalize common scheme typos inside URLs before extraction
    pre = re.sub(r"\bhtpps?://", "https://", pre, flags=re.IGNORECASE)
    pre = re.sub(r"\b(https?)//:", r"\1://", pre, flags=re.IGNORECASE)

    endpoints: set[str] = set()
    url_spans: list[tuple[int, int]] = []

    verb_prefix_re = re.compile(r"\b(GET|POST|PUT|DELETE|PATCH)\s+(?:para\s+|to\s+|em\s+|on\s+|at\s+)?$", re.IGNORECASE)
    cue_prefix_re = re.compile(r"\b(?:endpoint|rota|chamada|path|requisi[cç][aã]o|request|consultar|consultando)\s*(?:=|:|em|para|to)?\s*$", re.IGNORECASE)

    # 1. Extract API endpoints embedded inside full HTTP/HTTPS URLs
    for m in re.finditer(r"https?://[^\s<>\"'()\]]+", pre, flags=re.IGNORECASE):
        raw_u = m.group(0).rstrip(".,;:!?)>\"'")
        url_spans.append((m.start(), m.end()))
        before = pre[max(0, m.start() - 32) : m.start()]
        vm = verb_prefix_re.search(before)
        verb = vm.group(1).lower() if vm else ""
        has_cue = bool(vm or cue_prefix_re.search(before))

        try:
            u_clean = re.sub(r"^(https?://)[^/@\s]+@", r"\1", raw_u, flags=re.IGNORECASE)
            sp = urlsplit(u_clean)
            host_check = (sp.hostname or "").strip().lower().rstrip(".")
        except ValueError:
            continue
        if not host_check or host_check in IGNORED_ATTACHMENT_HOSTS:
            continue
        exact_host = host_check
        stripped_www = re.sub(r"^www\.", "", host_check)
        if "." in stripped_www:
            exact_host = stripped_www

        raw_pq = sp.path or "/"
        if sp.query:
            raw_pq = f"{raw_pq}?{sp.query}"
        is_api_host = exact_host.startswith(("api.", "graphql.", "gateway.", "backend."))
        if not (_API_PATH_PREFIX_RE.match(sp.path or "") or (is_api_host and raw_pq != "/") or (has_cue and raw_pq != "/")):
            continue
        canon_ep = _canonicalize_endpoint(raw_pq)
        if canon_ep:
            endpoints.add(canon_ep)
            endpoints.add(f"ephost:{exact_host}")
            if verb:
                endpoints.add(f"method:{verb}:{canon_ep}")

    # Mask out full URL spans before scanning relative/isolated paths so host names or URL fragments aren't double-parsed
    masked = list(pre)
    for s_idx, e_idx in url_spans:
        for i in range(s_idx, e_idx):
            masked[i] = " "
    rel_src = strip_accents("".join(masked))

    # 2. HTTP method + relative or root path
    for m in re.finditer(
        r"\b(GET|POST|PUT|DELETE|PATCH)\s+(?:para\s+|to\s+|em\s+|on\s+|at\s+)?(/?[a-zA-Z0-9._~%-]+(?:/[a-zA-Z0-9._~%-]+)*(?:\?[^\s<>\"'()\]]+)?)",
        rel_src,
        flags=re.IGNORECASE,
    ):
        verb = m.group(1).lower()
        cand_ep = m.group(2)
        if "/" not in cand_ep:
            continue
        canon_ep = _canonicalize_endpoint(cand_ep)
        if canon_ep:
            endpoints.add(canon_ep)
            endpoints.add(f"method:{verb}:{canon_ep}")

    # 3. Explicit cues ("endpoint /...", "rota graphql/chapters", "chamada v2/chapters")
    for m in re.finditer(
        r"\b(?:endpoint|rota|chamada|path|requisi[cç][aã]o|request)\s*(?:=|:|em|para|to)?\s*(/?[a-zA-Z0-9._~%-]+(?:/[a-zA-Z0-9._~%-]+)+(?:\?[^\s<>\"'()\]]+)?)",
        rel_src,
        flags=re.IGNORECASE,
    ):
        canon_ep = _canonicalize_endpoint(m.group(1))
        if canon_ep:
            endpoints.add(canon_ep)

    # 4. Standalone technical paths starting with API prefixes (e.g. /api/search, /api/v2/chapters?page=1, /maintenance/blocked)
    for m in re.finditer(
        r"(?:^|[\s('\"=,])(/(?:api|v\d+(?:\.\d+)?|graphql|gql|rest|rpc|ajax|wp-json|chapters?|episodes?|search|details?|auth|login|token|stream|manifest|hls|maintenance)(?:/[a-zA-Z0-9._~%-]+)+(?:\?[^\s<>\"'()\]]+)?)",
        rel_src,
        flags=re.IGNORECASE,
    ):
        canon_ep = _canonicalize_endpoint(m.group(1))
        if canon_ep:
            endpoints.add(canon_ep)

    return frozenset(endpoints)


def has_substantive_observations_text(
    text: str,
    *,
    extension_already_exists: bool = False,
    feature_topics: frozenset[str] = frozenset(),
    symptom_topics: frozenset[str] = frozenset(),
    existing_only_languages: frozenset[str] = frozenset(),
    error_signatures: frozenset[str] = frozenset(),
    category_mismatch: bool = False,
) -> bool:
    """Return True when observations contain concrete functional/technical divergences, not praise/background."""
    if extension_already_exists or existing_only_languages or symptom_topics or error_signatures or category_mismatch:
        return True
    norm = normalize_text(text, preserve_urls=False)
    if not norm or len(norm) < 12:
        return False
    if _NON_SUBSTANTIVE_NOTE_RE.match(norm) and len(norm) < 60:
        if not re.search(r"\b(?:sem|nao|apenas|somente|bloquear|ocultar|exigir|dispensar|obrigatori\w+|opcional)\b", norm):
            return False
    # Ignore pure praise/thank-you notes without functional constraints
    if re.match(r"^(?:quero\s+agradecer|gostaria\s+de\s+agradecer|muito\s+obrigad[oa]|parabens)\b", norm) and not re.search(
        r"\b(?:filtro[s]?|login|autenticacao|adulto|nsfw|crescente|decrescente|ordenar|agrupar|sem\s+\w+|bloquear|ocultar|obrigatori\w+|opcional)\b",
        norm,
    ):
        return False
    # Check for functional requirements/constraints (imperative verbs, ordering/grouping criteria, polarity/modality/visibility/restriction)
    if re.search(
        r"\b(?:adicionar\s+suporte|precis\w+|gostaria|quero|queria|suporte\s+(?:a|ao|para)|apenas\s+(?:em|por|com|sem|para|no|na|portugues|ingles|espanhol|manga[s]?|anime[s]?|novel[s]?)|somente\s+(?:em|por|com|sem|para|no|na|portugues|ingles|espanhol|manga[s]?|anime[s]?|novel[s]?)|exclusivamente|unicamente|corrigir|consertar|arrumar|resolver|incluir|excluir|exclui\w*|remover|omitir|implementar|faltando|separar|separad[oa]s?|dividir|unificar|filtrar\s+por|filtro\s+(?:de|por)|ordenar|ordenad[oa]s?|ordena[cç][aã]o|ordem\s+(?:de|por|crescente|decrescente|cronologica)|crescente|decrescente|ascendente|descendente|agrupar|agrupad[oa]s?|agrupamento|listad[oa]s?\s+por|exibir|mostrar|ocultar|esconder|permitir|bloquear|proibir|desativar|desabilitar|dispensar|exigir|op[cç][aã]o\s+(?:de|para)|configura[cç][aã]o|deve(?:m|ra|ria)?\s+(?:ter|ser|exibir|mostrar|ocultar|permitir|bloquear|incluir|excluir|ordenar|agrupar|filtrar)|necessari[oa]\s+que|requisito[s]?|obrigatori\w+|opcional|opcionalmente|facultativ\w+|sem\s+(?:filtro[s]?|login|autenticacao|cadastro|anuncio[s]?|propaganda[s]?|nsfw|conteudo|adulto|paywall|vip|bloquear|ocultar|excluir|permitir|exibir|incluir)|com\s+(?:filtro[s]?|login|autenticacao|cadastro|legenda[s]?|dublagem|hd|4k)|(?:nunca|jamais)\s+\w+|nao\s+(?:exibe|mostra|tem|funciona|carrega|reproduz|permitir|permite|incluir|inclui|exigir|exige|bloquear|bloqueia|ocultar|oculta|esconder|esconde|excluir|exclui|remover|remove|dispensar|dispensa|filtrar|exibir))\b",
        norm,
    ):
        return True
    return False


def infer_semantic_intent(
    *,
    form_category: str,
    clean_title: str,
    free_text: str,
    extension_already_exists: bool,
    error_signatures: frozenset[str],
    feature_topics: frozenset[str],
    symptom_topics: frozenset[str],
    target_languages: frozenset[str],
    existing_only_languages: frozenset[str],
    has_old_url: bool,
    has_new_url: bool,
    dead_evidence: str,
) -> tuple[str, frozenset[str], str, bool]:
    """Infer primary semantic intent, secondary intents, inferred category, and mismatch flag."""
    norm = normalize_text(f"{clean_title} {free_text}", preserve_urls=False)
    secondaries: set[str] = set()

    has_bug_verb = bool(
        re.search(
            r"\b(?:parou\s+de\s+funcionar|nao\s+funciona|quebrou|nao\s+carrega|nao\s+abre|nao\s+reproduz(?:iu|ia|em|iram)?|erro\s+(?:no|na|ao|http)|falha|no\s+pages\s+found|no\s+results\s+found|carregamento\s+infinito)\b",
            norm,
        )
    )
    has_enhancement_verb = bool(
        re.search(
            r"\b(?:adicionar\s+suporte|suporte\s+(?:a|ao|para)|incluir\s+(?:o|a|os|as|suporte|filtro|idioma|conteudo)|implementar|melhoria|melhorar|recurso|funcionalidade|tambem\s+consiga|acessar\s+o\s+conteudo\s+em|organiza\s+os\s+conteudos)\b",
            norm,
        )
    )
    has_create_verb = bool(
        re.search(
            r"\b(?:criar\s+(?:uma\s+)?extensao|nova\s+extensao|adicionar\s+(?:a\s+)?extensao|adicionar\s+(?:o\s+)?site|adicionem\s+essa\s+extensao)\b",
            norm,
        )
    )
    has_domain_cue = bool(
        (has_old_url and has_new_url)
        or (
            not has_bug_verb
            and re.search(r"\b(?:mudou\s+de\s+dominio|novo\s+dominio|mudanca\s+de\s+dominio|novo\s+endereco)\b", norm)
        )
    )
    has_loading_cue = bool(
        symptom_topics & {"no_pages_or_empty", "infinite_loading", "slow_download", "missing_content"}
        or error_signatures & {"no_pages_found", "no_results_found", "timeout", "video_playback"}
    )

    if error_signatures or has_bug_verb:
        secondaries.add("loading_issue" if has_loading_cue else "bug_fix")
    if (feature_topics - {"lang_locale"}) or has_enhancement_verb:
        secondaries.add("feature_addition")
    if (
        ("lang_locale" in feature_topics and (extension_already_exists or has_enhancement_verb or bool(existing_only_languages)))
        or (existing_only_languages and target_languages and not (target_languages <= existing_only_languages))
    ):
        secondaries.add("language_support")
    if "missing_content" in symptom_topics or re.search(r"\bnao\s+(?:exibe|mostra)\s+(?:o\s+)?conteudo\b", norm):
        secondaries.add("missing_content")
    if has_domain_cue:
        secondaries.add("domain_change")

    if form_category == "new_extension":
        if has_domain_cue:
            return "domain_change", frozenset(secondaries), "domain_change", True
        if dead_evidence == "permanent" and extension_already_exists:
            return "dead_source", frozenset(secondaries), "dead_source", True
        if "language_support" in secondaries and (extension_already_exists or has_enhancement_verb):
            return "language_support", frozenset(secondaries), "enhancement", True
        if (error_signatures or has_bug_verb) and not ("missing_content" in secondaries and has_enhancement_verb) and (
            extension_already_exists or not has_create_verb
        ):
            if error_signatures - {"cloudflare", "login_auth"} or has_bug_verb:
                primary = "loading_issue" if has_loading_cue else "bug_fix"
                return primary, frozenset(secondaries), "bug", True
        if extension_already_exists or has_enhancement_verb:
            if "missing_content" in secondaries and not ((feature_topics - {"lang_locale"}) and has_enhancement_verb):
                return "missing_content", frozenset(secondaries), "enhancement", True
            return "feature_addition", frozenset(secondaries), "enhancement", True
        return "create_extension", frozenset(secondaries), "new_extension", False

    if form_category == "bug":
        if has_enhancement_verb and not error_signatures and not has_bug_verb:
            primary = "language_support" if "language_support" in secondaries else "feature_addition"
            return primary, frozenset(secondaries), "enhancement", True
        if has_domain_cue and not has_loading_cue and not error_signatures:
            return "domain_change", frozenset(secondaries), "domain_change", True
        primary = "loading_issue" if has_loading_cue else "bug_fix"
        return primary, frozenset(secondaries), "bug", False

    if form_category == "domain_change":
        if has_bug_verb and not has_new_url and not re.search(r"\bmudou\s+de\s+dominio\b|\bnovo\s+dominio\b", norm):
            return ("loading_issue" if has_loading_cue else "bug_fix"), frozenset(secondaries), "bug", True
        return "domain_change", frozenset(secondaries), "domain_change", False

    if form_category == "dead_source":
        if has_domain_cue and dead_evidence != "permanent":
            return "domain_change", frozenset(secondaries), "domain_change", True
        return "dead_source", frozenset(secondaries), "dead_source", False

    if form_category == "enhancement":
        if has_create_verb and not extension_already_exists and not (feature_topics - {"nsfw_adult"}):
            return "create_extension", frozenset(secondaries), "new_extension", True
        if (error_signatures or has_bug_verb) and not has_enhancement_verb:
            return ("loading_issue" if has_loading_cue else "bug_fix"), frozenset(secondaries), "bug", True
        if "language_support" in secondaries and not (feature_topics - {"lang_locale"}):
            return "language_support", frozenset(secondaries), "enhancement", False
        return "feature_addition", frozenset(secondaries), "enhancement", False

    return "create_extension" if has_create_verb else "bug_fix", frozenset(secondaries), form_category, False
