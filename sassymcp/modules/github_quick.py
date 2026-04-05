"""SassyMCP GitHub Quick - Lean daily-driver GitHub tools.

Registers ONLY the most-used tools for daily development workflow.
Use github_ops for the full toolset. Both share the same _github_client.

Tools: push_files, get_file, create_issue, list_issues, create_pr, protect_branch
(6 tools vs 80 in github_ops — massive context window savings)
"""

import base64
import json
import logging
from typing import Any

logger = logging.getLogger("sassymcp.github_quick")

from sassymcp.modules._github_client import get_client, GitHubAPIError


def _ok(data: Any) -> str:
    if isinstance(data, str):
        return data
    return json.dumps(data, indent=2, default=str)


def _err(msg: str) -> str:
    return json.dumps({"error": msg})


def register(server):
    """Register lean GitHub tools for daily workflow."""

    _gh = None
    def _get_gh():
        nonlocal _gh
        if _gh is None:
            _gh = get_client()
        return _gh

    @server.tool()
    async def sassy_ghq_push(
        owner: str, repo: str, branch: str, message: str, files: str
    ) -> str:
        """Push files atomically (create or update). THE daily-driver tool.

        files: JSON array of {\"path\": \"...\", \"content\": \"...\"} objects.
        Uses Git Data API — no SHA bugs, no ETag nonsense.
        """
        try:
            file_list = json.loads(files) if isinstance(files, str) else files
            result = await _get_gh().push_files_atomic(owner, repo, branch, file_list, message)
            return _ok(result)
        except (json.JSONDecodeError, TypeError) as e:
            return _err(f"Invalid files JSON: {e}")
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_ghq_get(owner: str, repo: str, path: str, ref: str = "") -> str:
        """Get file contents + SHA from a repo."""
        try:
            params = {"ref": ref} if ref else {}
            resp = await _get_gh().get(f"repos/{owner}/{repo}/contents/{path}", params=params)
            data = _get_gh()._check(resp, f"get {path}")
            if isinstance(data, dict) and data.get("encoding") == "base64" and data.get("content"):
                try:
                    data["decoded_content"] = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
                    del data["content"]
                except Exception:
                    pass
            return _ok(data)
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_ghq_issue(
        owner: str, repo: str, title: str, body: str = "", labels: str = ""
    ) -> str:
        """Create an issue. labels: comma-separated."""
        try:
            payload: dict[str, Any] = {"title": title}
            if body:
                payload["body"] = body
            if labels:
                payload["labels"] = [l.strip() for l in labels.split(",")]
            resp = await _get_gh().post(f"repos/{owner}/{repo}/issues", json_body=payload)
            return _ok(_get_gh()._check(resp, "create issue"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_ghq_issues(owner: str, repo: str, state: str = "open", page: int = 1) -> str:
        """List issues."""
        try:
            resp = await _get_gh().get(
                f"repos/{owner}/{repo}/issues",
                params={"state": state, "page": page, "per_page": 30},
            )
            return _ok(_get_gh()._check(resp, "list issues"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_ghq_pr(
        owner: str, repo: str, title: str, head: str, base: str, body: str = ""
    ) -> str:
        """Create a pull request."""
        try:
            resp = await _get_gh().post(
                f"repos/{owner}/{repo}/pulls",
                json_body={"title": title, "head": head, "base": base, "body": body},
            )
            return _ok(_get_gh()._check(resp, "create PR"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_ghq_protect(owner: str, repo: str, branch: str = "main") -> str:
        """Protect a branch (no force push, no delete, enforce on admins)."""
        try:
            body = {
                "enforce_admins": True,
                "required_status_checks": None,
                "required_pull_request_reviews": None,
                "restrictions": None,
                "allow_force_pushes": False,
                "allow_deletions": False,
            }
            resp = await _get_gh().put(f"repos/{owner}/{repo}/branches/{branch}/protection", json_body=body)
            return _ok(_get_gh()._check(resp, "protect branch"))
        except GitHubAPIError as e:
            return _err(str(e))
