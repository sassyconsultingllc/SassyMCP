"""SassyMCP GitHub Client - Shared HTTP client for GitHub API.

Handles authentication, rate limiting, retries, and proper SHA management.
Used by both github_ops (full) and github_quick (lean) modules.

Requires GITHUB_TOKEN env var or GITHUB_PERSONAL_ACCESS_TOKEN.
"""

import asyncio
import base64
import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger("sassymcp.github")

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore


class GitHubAPIError(Exception):
    """GitHub API error with status code and message."""

    def __init__(self, status: int, message: str, url: str = ""):
        self.status = status
        self.url = url
        super().__init__(f"GitHub API {status}: {message} [{url}]")


class GitHubClient:
    """Async GitHub REST API client with retry logic and rate-limit awareness."""

    BASE = "https://api.github.com"

    def __init__(self):
        if httpx is None:
            raise RuntimeError("httpx not installed. Install with: pip install httpx")
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
        if not token:
            raise RuntimeError("Set GITHUB_TOKEN or GITHUB_PERSONAL_ACCESS_TOKEN env var")
        self._headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "SassyMCP/1.0.0",
        }
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=self._headers,
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
            )
        return self._client

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: Optional[dict] = None,
        retries: int = 3,
    ) -> httpx.Response:
        """Make an authenticated GitHub API request with retry + rate-limit handling."""
        client = await self._get_client()
        url = f"{self.BASE}/{path.lstrip('/')}" if not path.startswith("http") else path

        for attempt in range(retries):
            try:
                resp = await client.request(method, url, json=json_body, params=params)

                # Rate limit handling
                remaining = resp.headers.get("X-RateLimit-Remaining")
                if remaining and int(remaining) < 10:
                    logger.warning(f"GitHub rate limit low: {remaining} remaining")

                if resp.status_code == 403 and "rate limit" in resp.text.lower():
                    import time as _time
                    reset = resp.headers.get("X-RateLimit-Reset")
                    wait = max(int(reset or 0) - int(_time.time()), 5)
                    logger.warning(f"Rate limited, waiting {wait}s")
                    await asyncio.sleep(min(wait, 60))
                    continue

                # Retry on server errors
                if resp.status_code >= 500 and attempt < retries - 1:
                    wait = 2 ** attempt
                    logger.warning(f"GitHub {resp.status_code}, retry {attempt + 1} in {wait}s")
                    await asyncio.sleep(wait)
                    continue

                return resp

            except httpx.TransportError as e:
                if attempt < retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise GitHubAPIError(0, str(e), url) from e

        return resp  # type: ignore

    async def get(self, path: str, **kw) -> httpx.Response:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw) -> httpx.Response:
        return await self.request("POST", path, **kw)

    async def put(self, path: str, **kw) -> httpx.Response:
        return await self.request("PUT", path, **kw)

    async def patch(self, path: str, **kw) -> httpx.Response:
        return await self.request("PATCH", path, **kw)

    async def delete(self, path: str, **kw) -> httpx.Response:
        return await self.request("DELETE", path, **kw)

    def _check(self, resp: httpx.Response, context: str = "") -> dict | list | str:
        """Check response status, return parsed JSON or text."""
        if resp.status_code >= 400:
            try:
                body = resp.json()
                msg = body.get("message", resp.text[:500])
            except Exception:
                msg = resp.text[:500]
            raise GitHubAPIError(resp.status_code, f"{context}: {msg}", str(resp.url))
        try:
            return resp.json()
        except Exception:
            return resp.text

    # -- Helpers used by tools --

    async def get_file_sha(self, owner: str, repo: str, path: str, branch: str = "") -> Optional[str]:
        """Get the real blob SHA for a file (not ETag). Returns None if file doesn't exist."""
        params = {"ref": branch} if branch else {}
        resp = await self.get(f"repos/{owner}/{repo}/contents/{path}", params=params)
        if resp.status_code == 404:
            return None
        data = self._check(resp, f"get SHA for {path}")
        if isinstance(data, dict):
            return data.get("sha")
        return None

    async def push_files_atomic(
        self,
        owner: str,
        repo: str,
        branch: str,
        files: list[dict[str, str]],
        message: str,
    ) -> dict:
        """Push multiple files in a single atomic commit via Git Data API.

        files: list of {"path": "...", "content": "..."}
        Returns the updated ref object.
        """
        # 1. Get branch ref
        ref_resp = await self.get(f"repos/{owner}/{repo}/git/refs/heads/{branch}")
        ref_data = self._check(ref_resp, "get branch ref")
        base_sha = ref_data["object"]["sha"]

        # 2. Get base commit to find tree SHA
        commit_resp = await self.get(f"repos/{owner}/{repo}/git/commits/{base_sha}")
        commit_data = self._check(commit_resp, "get base commit")
        base_tree_sha = commit_data["tree"]["sha"]

        # 3. Create tree entries
        tree_entries = []
        for f in files:
            tree_entries.append({
                "path": f["path"],
                "mode": "100644",
                "type": "blob",
                "content": f["content"],
            })

        tree_resp = await self.post(
            f"repos/{owner}/{repo}/git/trees",
            json_body={"base_tree": base_tree_sha, "tree": tree_entries},
        )
        tree_data = self._check(tree_resp, "create tree")

        # 4. Create commit
        commit_resp = await self.post(
            f"repos/{owner}/{repo}/git/commits",
            json_body={
                "message": message,
                "tree": tree_data["sha"],
                "parents": [base_sha],
            },
        )
        new_commit = self._check(commit_resp, "create commit")

        # 5. Update ref
        update_resp = await self.patch(
            f"repos/{owner}/{repo}/git/refs/heads/{branch}",
            json_body={"sha": new_commit["sha"]},
        )
        return self._check(update_resp, "update ref")


# Module-level singleton
_client: Optional[GitHubClient] = None


def get_client() -> GitHubClient:
    """Get or create the shared GitHubClient singleton."""
    global _client
    if _client is None:
        _client = GitHubClient()
    return _client
