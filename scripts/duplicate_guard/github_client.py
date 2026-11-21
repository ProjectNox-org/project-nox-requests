# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Resilient GitHub REST API client using Python stdlib only."""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = "https://api.github.com"
_NEXT_LINK_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


class GitHubApiError(RuntimeError):
    """Base error for GitHub API failures."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class RateLimitError(GitHubApiError):
    """Raised when GitHub API rate limit is exceeded."""


class PermissionDeniedError(GitHubApiError):
    """Raised when token lacks required write/read permissions."""


class DryRunWriteBlockedError(RuntimeError):
    """Raised if a write operation is attempted while dry_run=True."""


class GitHubClient:
    """Minimal safe GitHub REST client with retries, pagination, and dry-run enforcement."""

    def __init__(
        self,
        repo: str,
        token: str | None = None,
        *,
        dry_run: bool = False,
        max_retries: int = 3,
        max_pages: int = 100,
        timeout_sec: int = 25,
        sleep_fn=time.sleep,
    ) -> None:
        if "/" not in repo:
            raise ValueError(f"Invalid repository slug '{repo}', expected 'owner/repo'.")
        self.repo = repo
        self._token = (token or "").strip()
        self.dry_run = dry_run
        self.max_retries = max(1, max_retries)
        self.max_pages = max(1, max_pages)
        self.timeout_sec = timeout_sec
        self._sleep = sleep_fn
        self.api_calls_made = 0

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ProjectNox-DuplicateGuard/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        *,
        allow_404: bool = False,
    ) -> tuple[int, dict[str, str], Any]:
        if method != "GET" and self.dry_run:
            raise DryRunWriteBlockedError(f"Blocked {method} {url} because dry_run=True")

        body_bytes = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = self._headers()
        if body_bytes is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"

        for attempt in range(1, self.max_retries + 1):
            req = Request(url, data=body_bytes, headers=headers, method=method)
            self.api_calls_made += 1
            try:
                with urlopen(req, timeout=self.timeout_sec) as resp:  # nosec B310: fixed https://api.github.com base
                    raw_text = resp.read().decode("utf-8", errors="replace")
                    resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                    parsed = json.loads(raw_text) if raw_text.strip() else None
                    return resp.status, resp_headers, parsed
            except HTTPError as exc:
                err_headers = {k.lower(): v for k, v in (exc.headers.items() if exc.headers else ())}
                err_body = ""
                try:
                    err_body = exc.read().decode("utf-8", errors="replace")[:400]
                except Exception:
                    pass

                if exc.code == 404 and allow_404:
                    return 404, err_headers, None

                remaining = err_headers.get("x-ratelimit-remaining")
                if exc.code == 429 or (exc.code == 403 and (remaining == "0" or "rate limit" in err_body.lower())):
                    raise RateLimitError(
                        f"GitHub API rate limit exceeded (HTTP {exc.code}, remaining={remaining})",
                        status=exc.code,
                    ) from exc

                if exc.code in (401, 403):
                    raise PermissionDeniedError(
                        f"GitHub API permission denied (HTTP {exc.code}): {err_body}",
                        status=exc.code,
                    ) from exc

                if exc.code in (500, 502, 503, 504) and attempt < self.max_retries:
                    self._sleep(min(8.0, float(2 ** (attempt - 1))))
                    continue

                raise GitHubApiError(
                    f"GitHub API HTTP {exc.code} on {method} {url}: {err_body}",
                    status=exc.code,
                ) from exc
            except (URLError, TimeoutError) as exc:
                if attempt < self.max_retries:
                    self._sleep(min(8.0, float(2 ** (attempt - 1))))
                    continue
                raise GitHubApiError(f"Network error on {method} {url}: {exc}") from exc

        raise GitHubApiError(f"Exhausted retries on {method} {url}")

    def _paginate(self, initial_url: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        url: str | None = initial_url
        page = 0
        while url:
            page += 1
            if page > self.max_pages:
                raise GitHubApiError(f"Exceeded max_pages={self.max_pages} safety ceiling")
            _, headers, data = self._request("GET", url)
            if not isinstance(data, list):
                raise GitHubApiError(f"Expected list response from {url}, got {type(data).__name__}")
            results.extend(data)
            link_hdr = headers.get("link", "")
            m_next = _NEXT_LINK_RE.search(link_hdr)
            if m_next:
                url = m_next.group(1)
            elif len(data) == 100 and "page=" in url:
                # Fallback when Link header is absent in mock/proxy responses
                next_page = page + 1
                url = re.sub(r"([?&]page=)\d+", rf"\g<1>{next_page}", url)
            else:
                url = None
        return results

    def list_issues(self, *, state: str = "all") -> list[dict[str, Any]]:
        """List all non-PR issues in the repository with deterministic deduplication."""
        query = urlencode({
            "state": state,
            "per_page": 100,
            "page": 1,
            "sort": "created",
            "direction": "desc",
        })
        items = self._paginate(f"{API_BASE}/repos/{self.repo}/issues?{query}")
        seen: set[int] = set()
        issues: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict) or "pull_request" in item:
                continue
            num = int(item["number"])
            if num not in seen:
                seen.add(num)
                issues.append(item)
        return issues

    def get_issue(self, issue_number: int) -> dict[str, Any]:
        """Fetch live state of a single issue."""
        _, _, data = self._request("GET", f"{API_BASE}/repos/{self.repo}/issues/{int(issue_number)}")
        if not isinstance(data, dict):
            raise GitHubApiError(f"Unexpected issue payload for #{issue_number}")
        return data

    def list_comments(self, issue_number: int) -> list[dict[str, Any]]:
        """List all comments for an issue using full pagination."""
        query = urlencode({"per_page": 100, "page": 1})
        url = f"{API_BASE}/repos/{self.repo}/issues/{int(issue_number)}/comments?{query}"
        return self._paginate(url)

    def create_comment(self, issue_number: int, body: str) -> dict[str, Any]:
        """Create a new issue comment."""
        _, _, data = self._request(
            "POST",
            f"{API_BASE}/repos/{self.repo}/issues/{int(issue_number)}/comments",
            payload={"body": body},
        )
        return data if isinstance(data, dict) else {}

    def update_comment(self, comment_id: int, body: str) -> dict[str, Any]:
        """Update an existing issue comment in place."""
        _, _, data = self._request(
            "PATCH",
            f"{API_BASE}/repos/{self.repo}/issues/comments/{int(comment_id)}",
            payload={"body": body},
        )
        return data if isinstance(data, dict) else {}

    def add_labels(self, issue_number: int, labels: list[str]) -> None:
        """Add labels to an issue."""
        if not labels:
            return
        self._request(
            "POST",
            f"{API_BASE}/repos/{self.repo}/issues/{int(issue_number)}/labels",
            payload={"labels": labels},
        )

    def remove_label(self, issue_number: int, label_name: str) -> None:
        """Remove a label if present (ignores 404)."""
        from urllib.parse import quote

        encoded = quote(label_name, safe="")
        self._request(
            "DELETE",
            f"{API_BASE}/repos/{self.repo}/issues/{int(issue_number)}/labels/{encoded}",
            allow_404=True,
        )

    def close_issue(self, issue_number: int, *, state_reason: str = "duplicate") -> dict[str, Any]:
        """Close an issue with formal state_reason, falling back safely if 'duplicate' returns 422."""
        url = f"{API_BASE}/repos/{self.repo}/issues/{int(issue_number)}"
        try:
            _, _, data = self._request("PATCH", url, payload={"state": "closed", "state_reason": state_reason})
            return data if isinstance(data, dict) else {}
        except GitHubApiError as exc:
            if exc.status == 422 and state_reason == "duplicate":
                _, _, data = self._request("PATCH", url, payload={"state": "closed", "state_reason": "not_planned"})
                return data if isinstance(data, dict) else {}
            raise

    def fetch_catalog_json(self, repo_slug: str, path: str = "index.min.json") -> list[dict[str, Any]]:
        """Fetch and decode index.min.json from an official Project Nox catalog repository."""
        allowed_repos = {"ProjectNox-org/manga-extensoes", "ProjectNox-org/anime-extensoes"}
        if repo_slug not in allowed_repos:
            raise ValueError(f"Unsupported catalog repository: {repo_slug}")
        import base64

        _, _, data = self._request("GET", f"{API_BASE}/repos/{repo_slug}/contents/{path}")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            content_b64 = data.get("content")
            if isinstance(content_b64, str) and content_b64.strip():
                raw_json = base64.b64decode(content_b64).decode("utf-8", errors="replace")
                parsed = json.loads(raw_json)
                if isinstance(parsed, list):
                    return [item for item in parsed if isinstance(item, dict)]
            blob_sha = data.get("sha")
            if isinstance(blob_sha, str) and blob_sha:
                _, _, blob = self._request("GET", f"{API_BASE}/repos/{repo_slug}/git/blobs/{blob_sha}")
                if isinstance(blob, dict) and isinstance(blob.get("content"), str):
                    raw_json = base64.b64decode(blob["content"]).decode("utf-8", errors="replace")
                    parsed = json.loads(raw_json)
                    if isinstance(parsed, list):
                        return [item for item in parsed if isinstance(item, dict)]
        return []

    def list_catalog_extensions(self) -> dict[str, list[dict[str, Any]]]:
        """Load published extension indexes from manga-extensoes and anime-extensoes safely."""
        catalog: dict[str, list[dict[str, Any]]] = {"manga": [], "anime": []}
        targets = (
            ("manga", "ProjectNox-org/manga-extensoes"),
            ("anime", "ProjectNox-org/anime-extensoes"),
        )
        for key, slug in targets:
            try:
                catalog[key] = self.fetch_catalog_json(slug, "index.min.json")
            except (GitHubApiError, ValueError, json.JSONDecodeError):
                catalog[key] = []
        return catalog
