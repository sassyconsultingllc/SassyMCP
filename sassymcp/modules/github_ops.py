"""SassyMCP GitHub Operations - Full GitHub API toolset.

Complete replacement for the official GitHub MCP server, with proper SHA
handling (blob SHA, not ETag), correct path encoding, and retry logic.

Covers: repos, issues, PRs, branches, tags, releases, commits, search,
code scanning, secret scanning, dependabot, notifications, discussions,
actions, security advisories, gists, projects, labels, users, orgs, stars.

Requires: GITHUB_TOKEN env var, httpx (pip install httpx)
"""

import base64
import json
import logging
from typing import Any, Optional

logger = logging.getLogger("sassymcp.github_ops")

from sassymcp.modules._github_client import get_client, GitHubAPIError


def _ok(data: Any) -> str:
    if isinstance(data, str):
        return data
    return json.dumps(data, indent=2, default=str)


def _err(msg: str) -> str:
    return json.dumps({"error": msg})


def register(server):
    """Register all GitHub tools — 80 tools covering full GitHub API."""

    _gh = None
    def _get_gh():
        nonlocal _gh
        if _gh is None:
            _gh = get_client()
        return _gh

    # ══════════════════════════════════════════════════════════════════
    # CONTEXT
    # ══════════════════════════════════════════════════════════════════

    @server.tool()
    async def sassy_gh_get_me() -> str:
        """Get the authenticated GitHub user's profile."""
        try:
            resp = await _get_gh().get("user")
            return _ok(_get_gh()._check(resp, "get user"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_teams(org: str) -> str:
        """List teams for an organization."""
        try:
            resp = await _get_gh().get(f"orgs/{org}/teams")
            return _ok(_get_gh()._check(resp, "list teams"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_team_members(org: str, team_slug: str) -> str:
        """List members of a team."""
        try:
            resp = await _get_gh().get(f"orgs/{org}/teams/{team_slug}/members")
            return _ok(_get_gh()._check(resp, "list team members"))
        except GitHubAPIError as e:
            return _err(str(e))

    # ══════════════════════════════════════════════════════════════════
    # REPOSITORIES
    # ══════════════════════════════════════════════════════════════════

    @server.tool()
    async def sassy_gh_search_repos(query: str, page: int = 1, per_page: int = 30) -> str:
        """Search GitHub repositories."""
        try:
            resp = await _get_gh().get("search/repositories", params={"q": query, "page": page, "per_page": per_page})
            return _ok(_get_gh()._check(resp, "search repos"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_file_contents(owner: str, repo: str, path: str = "", ref: str = "") -> str:
        """Get file or directory contents. Returns decoded content + SHA."""
        try:
            params = {}
            if ref:
                params["ref"] = ref
            resp = await _get_gh().get(f"repos/{owner}/{repo}/contents/{path}", params=params)
            data = _get_gh()._check(resp, f"get contents {path}")
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
    async def sassy_gh_create_file(owner: str, repo: str, path: str, content: str, message: str, branch: str) -> str:
        """Create a NEW file. Use push_files or update_file for existing files."""
        try:
            encoded = base64.b64encode(content.encode()).decode()
            body = {"message": message, "content": encoded, "branch": branch}
            resp = await _get_gh().put(f"repos/{owner}/{repo}/contents/{path}", json_body=body)
            return _ok(_get_gh()._check(resp, "create file"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_update_file(owner: str, repo: str, path: str, content: str, message: str, branch: str, sha: str = "") -> str:
        """Update EXISTING file. Auto-fetches blob SHA if not provided."""
        try:
            if not sha:
                sha = await _get_gh().get_file_sha(owner, repo, path, branch)
                if not sha:
                    return _err(f"File {path} not found on {branch}. Use create_file.")
            encoded = base64.b64encode(content.encode()).decode()
            body = {"message": message, "content": encoded, "branch": branch, "sha": sha}
            resp = await _get_gh().put(f"repos/{owner}/{repo}/contents/{path}", json_body=body)
            return _ok(_get_gh()._check(resp, "update file"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_delete_file(owner: str, repo: str, path: str, message: str, branch: str, sha: str = "") -> str:
        """Delete a file from a repository."""
        try:
            if not sha:
                sha = await _get_gh().get_file_sha(owner, repo, path, branch)
                if not sha:
                    return _err(f"File {path} not found on {branch}")
            body = {"message": message, "sha": sha, "branch": branch}
            resp = await _get_gh().delete(f"repos/{owner}/{repo}/contents/{path}", json_body=body)
            return _ok(_get_gh()._check(resp, "delete file"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_push_files(owner: str, repo: str, branch: str, message: str, files: str) -> str:
        """Push multiple files atomically via Git Data API. PREFERRED for all ops.
        files: JSON array of {"path": "...", "content": "..."} objects."""
        try:
            file_list = json.loads(files) if isinstance(files, str) else files
            result = await _get_gh().push_files_atomic(owner, repo, branch, file_list, message)
            return _ok(result)
        except (json.JSONDecodeError, TypeError) as e:
            return _err(f"Invalid files JSON: {e}")
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_create_repo(name: str, description: str = "", private: bool = True, auto_init: bool = False, org: str = "") -> str:
        """Create a new GitHub repository."""
        try:
            body: dict[str, Any] = {"name": name, "description": description, "private": private, "auto_init": auto_init}
            path = f"orgs/{org}/repos" if org else "user/repos"
            resp = await _get_gh().post(path, json_body=body)
            return _ok(_get_gh()._check(resp, "create repo"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_fork_repo(owner: str, repo: str, org: str = "") -> str:
        """Fork a repository."""
        try:
            body = {"organization": org} if org else {}
            resp = await _get_gh().post(f"repos/{owner}/{repo}/forks", json_body=body)
            return _ok(_get_gh()._check(resp, "fork repo"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_update_repo(owner: str, repo: str, settings: str) -> str:
        """Update repo settings. settings: JSON e.g. {"visibility":"public"}"""
        try:
            body = json.loads(settings) if isinstance(settings, str) else settings
            resp = await _get_gh().patch(f"repos/{owner}/{repo}", json_body=body)
            return _ok(_get_gh()._check(resp, "update repo"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_commit(owner: str, repo: str, sha: str) -> str:
        """Get details for a specific commit."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/commits/{sha}")
            return _ok(_get_gh()._check(resp, "get commit"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_list_commits(owner: str, repo: str, sha: str = "", author: str = "", page: int = 1, per_page: int = 30) -> str:
        """List commits on a branch."""
        try:
            params: dict[str, Any] = {"page": page, "per_page": per_page}
            if sha: params["sha"] = sha
            if author: params["author"] = author
            resp = await _get_gh().get(f"repos/{owner}/{repo}/commits", params=params)
            return _ok(_get_gh()._check(resp, "list commits"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_list_branches(owner: str, repo: str, page: int = 1, per_page: int = 30) -> str:
        """List branches in a repository."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/branches", params={"page": page, "per_page": per_page})
            return _ok(_get_gh()._check(resp, "list branches"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_create_branch(owner: str, repo: str, branch: str, from_branch: str = "") -> str:
        """Create a new branch."""
        try:
            if not from_branch:
                repo_resp = await _get_gh().get(f"repos/{owner}/{repo}")
                repo_data = _get_gh()._check(repo_resp, "get repo")
                from_branch = repo_data["default_branch"]
            ref_resp = await _get_gh().get(f"repos/{owner}/{repo}/git/refs/heads/{from_branch}")
            ref_data = _get_gh()._check(ref_resp, "get source branch")
            body = {"ref": f"refs/heads/{branch}", "sha": ref_data["object"]["sha"]}
            resp = await _get_gh().post(f"repos/{owner}/{repo}/git/refs", json_body=body)
            return _ok(_get_gh()._check(resp, "create branch"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_list_tags(owner: str, repo: str, page: int = 1, per_page: int = 30) -> str:
        """List tags in a repository."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/tags", params={"page": page, "per_page": per_page})
            return _ok(_get_gh()._check(resp, "list tags"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_tag(owner: str, repo: str, tag: str) -> str:
        """Get details for a specific tag."""
        try:
            ref_resp = await _get_gh().get(f"repos/{owner}/{repo}/git/refs/tags/{tag}")
            ref_data = _get_gh()._check(ref_resp, "get tag ref")
            tag_resp = await _get_gh().get(f"repos/{owner}/{repo}/git/tags/{ref_data['object']['sha']}")
            return _ok(_get_gh()._check(tag_resp, "get tag"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_list_releases(owner: str, repo: str, page: int = 1, per_page: int = 30) -> str:
        """List releases."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/releases", params={"page": page, "per_page": per_page})
            return _ok(_get_gh()._check(resp, "list releases"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_latest_release(owner: str, repo: str) -> str:
        """Get latest release."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/releases/latest")
            return _ok(_get_gh()._check(resp, "get latest release"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_release_by_tag(owner: str, repo: str, tag: str) -> str:
        """Get release by tag name."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/releases/tags/{tag}")
            return _ok(_get_gh()._check(resp, "get release"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_list_starred(username: str = "", page: int = 1, per_page: int = 30) -> str:
        """List starred repos. Empty username = authenticated user."""
        try:
            path = f"users/{username}/starred" if username else "user/starred"
            resp = await _get_gh().get(path, params={"page": page, "per_page": per_page})
            return _ok(_get_gh()._check(resp, "list starred"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_star_repo(owner: str, repo: str) -> str:
        """Star a repository."""
        try:
            await _get_gh().put(f"user/starred/{owner}/{repo}")
            return _ok({"status": "starred", "repo": f"{owner}/{repo}"})
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_unstar_repo(owner: str, repo: str) -> str:
        """Unstar a repository."""
        try:
            await _get_gh().delete(f"user/starred/{owner}/{repo}")
            return _ok({"status": "unstarred", "repo": f"{owner}/{repo}"})
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_tree(owner: str, repo: str, sha: str, recursive: bool = False) -> str:
        """Get a Git tree. recursive=True for full listing."""
        try:
            params = {"recursive": "1"} if recursive else {}
            resp = await _get_gh().get(f"repos/{owner}/{repo}/git/trees/{sha}", params=params)
            return _ok(_get_gh()._check(resp, "get tree"))
        except GitHubAPIError as e:
            return _err(str(e))

    # ══════════════════════════════════════════════════════════════════
    # ISSUES
    # ══════════════════════════════════════════════════════════════════

    @server.tool()
    async def sassy_gh_get_issue(owner: str, repo: str, issue_number: int) -> str:
        """Get issue details."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/issues/{issue_number}")
            return _ok(_get_gh()._check(resp, "get issue"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_list_issues(owner: str, repo: str, state: str = "open", labels: str = "", sort: str = "created", direction: str = "desc", page: int = 1, per_page: int = 30) -> str:
        """List issues."""
        try:
            params: dict[str, Any] = {"state": state, "sort": sort, "direction": direction, "page": page, "per_page": per_page}
            if labels: params["labels"] = labels
            resp = await _get_gh().get(f"repos/{owner}/{repo}/issues", params=params)
            return _ok(_get_gh()._check(resp, "list issues"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_create_issue(owner: str, repo: str, title: str, body: str = "", labels: str = "", assignees: str = "") -> str:
        """Create issue. labels/assignees: comma-separated."""
        try:
            payload: dict[str, Any] = {"title": title}
            if body: payload["body"] = body
            if labels: payload["labels"] = [l.strip() for l in labels.split(",")]
            if assignees: payload["assignees"] = [a.strip() for a in assignees.split(",")]
            resp = await _get_gh().post(f"repos/{owner}/{repo}/issues", json_body=payload)
            return _ok(_get_gh()._check(resp, "create issue"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_update_issue(owner: str, repo: str, issue_number: int, title: str = "", body: str = "", state: str = "", labels: str = "", assignees: str = "") -> str:
        """Update an issue."""
        try:
            payload: dict[str, Any] = {}
            if title: payload["title"] = title
            if body: payload["body"] = body
            if state: payload["state"] = state
            if labels: payload["labels"] = [l.strip() for l in labels.split(",")]
            if assignees: payload["assignees"] = [a.strip() for a in assignees.split(",")]
            resp = await _get_gh().patch(f"repos/{owner}/{repo}/issues/{issue_number}", json_body=payload)
            return _ok(_get_gh()._check(resp, "update issue"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_add_issue_comment(owner: str, repo: str, issue_number: int, body: str) -> str:
        """Add a comment to an issue."""
        try:
            resp = await _get_gh().post(f"repos/{owner}/{repo}/issues/{issue_number}/comments", json_body={"body": body})
            return _ok(_get_gh()._check(resp, "add comment"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_search_issues(query: str, sort: str = "", order: str = "desc", page: int = 1, per_page: int = 30) -> str:
        """Search issues and PRs across GitHub."""
        try:
            params: dict[str, Any] = {"q": query, "order": order, "page": page, "per_page": per_page}
            if sort: params["sort"] = sort
            resp = await _get_gh().get("search/issues", params=params)
            return _ok(_get_gh()._check(resp, "search issues"))
        except GitHubAPIError as e:
            return _err(str(e))

    # ══════════════════════════════════════════════════════════════════
    # PULL REQUESTS
    # ══════════════════════════════════════════════════════════════════

    @server.tool()
    async def sassy_gh_get_pr(owner: str, repo: str, pull_number: int) -> str:
        """Get PR details."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/pulls/{pull_number}")
            return _ok(_get_gh()._check(resp, "get PR"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_list_prs(owner: str, repo: str, state: str = "open", sort: str = "created", direction: str = "desc", base: str = "", head: str = "", page: int = 1, per_page: int = 30) -> str:
        """List pull requests."""
        try:
            params: dict[str, Any] = {"state": state, "sort": sort, "direction": direction, "page": page, "per_page": per_page}
            if base: params["base"] = base
            if head: params["head"] = head
            resp = await _get_gh().get(f"repos/{owner}/{repo}/pulls", params=params)
            return _ok(_get_gh()._check(resp, "list PRs"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_create_pr(owner: str, repo: str, title: str, head: str, base: str, body: str = "", draft: bool = False) -> str:
        """Create a pull request."""
        try:
            resp = await _get_gh().post(f"repos/{owner}/{repo}/pulls", json_body={"title": title, "head": head, "base": base, "body": body, "draft": draft})
            return _ok(_get_gh()._check(resp, "create PR"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_update_pr(owner: str, repo: str, pull_number: int, title: str = "", body: str = "", state: str = "", base: str = "") -> str:
        """Update a PR."""
        try:
            payload: dict[str, Any] = {}
            if title: payload["title"] = title
            if body: payload["body"] = body
            if state: payload["state"] = state
            if base: payload["base"] = base
            resp = await _get_gh().patch(f"repos/{owner}/{repo}/pulls/{pull_number}", json_body=payload)
            return _ok(_get_gh()._check(resp, "update PR"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_merge_pr(owner: str, repo: str, pull_number: int, merge_method: str = "squash", commit_title: str = "", commit_message: str = "") -> str:
        """Merge a PR. merge_method: merge, squash, rebase."""
        try:
            payload: dict[str, Any] = {"merge_method": merge_method}
            if commit_title: payload["commit_title"] = commit_title
            if commit_message: payload["commit_message"] = commit_message
            resp = await _get_gh().put(f"repos/{owner}/{repo}/pulls/{pull_number}/merge", json_body=payload)
            return _ok(_get_gh()._check(resp, "merge PR"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_pr_files(owner: str, repo: str, pull_number: int) -> str:
        """Get files changed in a PR."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/pulls/{pull_number}/files")
            return _ok(_get_gh()._check(resp, "PR files"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_pr_reviews(owner: str, repo: str, pull_number: int) -> str:
        """Get reviews on a PR."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/pulls/{pull_number}/reviews")
            return _ok(_get_gh()._check(resp, "PR reviews"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_pr_review_comments(owner: str, repo: str, pull_number: int) -> str:
        """Get review comments on a PR."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/pulls/{pull_number}/comments")
            return _ok(_get_gh()._check(resp, "PR comments"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_create_pr_review(owner: str, repo: str, pull_number: int, event: str, body: str = "", comments: str = "") -> str:
        """Create PR review. event: APPROVE, REQUEST_CHANGES, COMMENT."""
        try:
            payload: dict[str, Any] = {"event": event}
            if body: payload["body"] = body
            if comments: payload["comments"] = json.loads(comments)
            resp = await _get_gh().post(f"repos/{owner}/{repo}/pulls/{pull_number}/reviews", json_body=payload)
            return _ok(_get_gh()._check(resp, "create review"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_update_pr_branch(owner: str, repo: str, pull_number: int) -> str:
        """Update PR branch with latest from base."""
        try:
            resp = await _get_gh().put(f"repos/{owner}/{repo}/pulls/{pull_number}/update-branch")
            return _ok(_get_gh()._check(resp, "update PR branch"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_pr_status(owner: str, repo: str, pull_number: int) -> str:
        """Get combined status checks for a PR."""
        try:
            pr_resp = await _get_gh().get(f"repos/{owner}/{repo}/pulls/{pull_number}")
            pr_data = _get_gh()._check(pr_resp, "get PR")
            resp = await _get_gh().get(f"repos/{owner}/{repo}/commits/{pr_data['head']['sha']}/status")
            return _ok(_get_gh()._check(resp, "PR status"))
        except GitHubAPIError as e:
            return _err(str(e))

    # ══════════════════════════════════════════════════════════════════
    # SEARCH
    # ══════════════════════════════════════════════════════════════════

    @server.tool()
    async def sassy_gh_search_code(query: str, page: int = 1, per_page: int = 30) -> str:
        """Search code across GitHub."""
        try:
            resp = await _get_gh().get("search/code", params={"q": query, "page": page, "per_page": per_page})
            return _ok(_get_gh()._check(resp, "search code"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_search_users(query: str, sort: str = "", order: str = "desc", page: int = 1, per_page: int = 30) -> str:
        """Search GitHub users."""
        try:
            params: dict[str, Any] = {"q": query, "order": order, "page": page, "per_page": per_page}
            if sort: params["sort"] = sort
            resp = await _get_gh().get("search/users", params=params)
            return _ok(_get_gh()._check(resp, "search users"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_search_orgs(query: str, page: int = 1, per_page: int = 30) -> str:
        """Search GitHub organizations."""
        try:
            resp = await _get_gh().get("search/users", params={"q": f"{query} type:org", "page": page, "per_page": per_page})
            return _ok(_get_gh()._check(resp, "search orgs"))
        except GitHubAPIError as e:
            return _err(str(e))

    # ══════════════════════════════════════════════════════════════════
    # CODE SCANNING / SECRET SCANNING / DEPENDABOT
    # ══════════════════════════════════════════════════════════════════

    @server.tool()
    async def sassy_gh_list_code_scanning(owner: str, repo: str, state: str = "open", page: int = 1) -> str:
        """List code scanning alerts."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/code-scanning/alerts", params={"state": state, "page": page})
            return _ok(_get_gh()._check(resp, "code scanning"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_code_scanning(owner: str, repo: str, alert_number: int) -> str:
        """Get specific code scanning alert."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/code-scanning/alerts/{alert_number}")
            return _ok(_get_gh()._check(resp, "code scanning alert"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_list_secret_scanning(owner: str, repo: str, state: str = "open", page: int = 1) -> str:
        """List secret scanning alerts."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/secret-scanning/alerts", params={"state": state, "page": page})
            return _ok(_get_gh()._check(resp, "secret scanning"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_secret_scanning(owner: str, repo: str, alert_number: int) -> str:
        """Get specific secret scanning alert."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/secret-scanning/alerts/{alert_number}")
            return _ok(_get_gh()._check(resp, "secret alert"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_list_dependabot(owner: str, repo: str, state: str = "open", page: int = 1) -> str:
        """List Dependabot alerts."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/dependabot/alerts", params={"state": state, "page": page})
            return _ok(_get_gh()._check(resp, "dependabot"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_dependabot(owner: str, repo: str, alert_number: int) -> str:
        """Get specific Dependabot alert."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/dependabot/alerts/{alert_number}")
            return _ok(_get_gh()._check(resp, "dependabot alert"))
        except GitHubAPIError as e:
            return _err(str(e))

    # ══════════════════════════════════════════════════════════════════
    # NOTIFICATIONS
    # ══════════════════════════════════════════════════════════════════

    @server.tool()
    async def sassy_gh_list_notifications(all_notifs: bool = False, page: int = 1) -> str:
        """List notifications."""
        try:
            params: dict[str, Any] = {"page": page}
            if all_notifs: params["all"] = "true"
            resp = await _get_gh().get("notifications", params=params)
            return _ok(_get_gh()._check(resp, "notifications"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_notification(thread_id: str) -> str:
        """Get notification thread."""
        try:
            resp = await _get_gh().get(f"notifications/threads/{thread_id}")
            return _ok(_get_gh()._check(resp, "notification"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_mark_notification_read(thread_id: str) -> str:
        """Mark notification read."""
        try:
            await _get_gh().patch(f"notifications/threads/{thread_id}")
            return _ok({"status": "read", "thread_id": thread_id})
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_mark_all_read() -> str:
        """Mark all notifications read."""
        try:
            await _get_gh().put("notifications", json_body={"read": True})
            return _ok({"status": "all_read"})
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_notification_sub(thread_id: str, ignored: bool = False) -> str:
        """Set notification subscription. ignored=True to mute."""
        try:
            resp = await _get_gh().put(f"notifications/threads/{thread_id}/subscription", json_body={"ignored": ignored})
            return _ok(_get_gh()._check(resp, "subscription"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_repo_notification_sub(owner: str, repo: str, ignored: bool = False) -> str:
        """Set repo notification subscription."""
        try:
            resp = await _get_gh().put(f"repos/{owner}/{repo}/subscription", json_body={"subscribed": not ignored, "ignored": ignored})
            return _ok(_get_gh()._check(resp, "repo subscription"))
        except GitHubAPIError as e:
            return _err(str(e))

    # ══════════════════════════════════════════════════════════════════
    # DISCUSSIONS (GraphQL)
    # ══════════════════════════════════════════════════════════════════

    @server.tool()
    async def sassy_gh_list_discussions(owner: str, repo: str, per_page: int = 10) -> str:
        """List discussions (GraphQL)."""
        try:
            q = "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){discussions(first:$n,orderBy:{field:UPDATED_AT,direction:DESC}){nodes{number title url author{login}createdAt category{name}answeredAt}}}}"
            resp = await _get_gh().post("graphql", json_body={"query": q, "variables": {"o": owner, "r": repo, "n": per_page}})
            data = _get_gh()._check(resp, "discussions")
            return _ok(data.get("data", {}).get("repository", {}).get("discussions", {}).get("nodes", []))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_discussion(owner: str, repo: str, number: int) -> str:
        """Get discussion by number (GraphQL)."""
        try:
            q = "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){discussion(number:$n){number title url body author{login}createdAt category{name}comments(first:20){nodes{body author{login}createdAt}}}}}"
            resp = await _get_gh().post("graphql", json_body={"query": q, "variables": {"o": owner, "r": repo, "n": number}})
            data = _get_gh()._check(resp, "discussion")
            return _ok(data.get("data", {}).get("repository", {}).get("discussion", {}))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_list_discussion_categories(owner: str, repo: str) -> str:
        """List discussion categories."""
        try:
            q = "query($o:String!,$r:String!){repository(owner:$o,name:$r){discussionCategories(first:25){nodes{id name description emoji}}}}"
            resp = await _get_gh().post("graphql", json_body={"query": q, "variables": {"o": owner, "r": repo}})
            data = _get_gh()._check(resp, "categories")
            return _ok(data.get("data", {}).get("repository", {}).get("discussionCategories", {}).get("nodes", []))
        except GitHubAPIError as e:
            return _err(str(e))

    # ══════════════════════════════════════════════════════════════════
    # ACTIONS (WORKFLOWS)
    # ══════════════════════════════════════════════════════════════════

    @server.tool()
    async def sassy_gh_list_runs(owner: str, repo: str, workflow_id: str = "", status: str = "", page: int = 1, per_page: int = 20) -> str:
        """List workflow runs."""
        try:
            path = f"repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs" if workflow_id else f"repos/{owner}/{repo}/actions/runs"
            params: dict[str, Any] = {"page": page, "per_page": per_page}
            if status: params["status"] = status
            resp = await _get_gh().get(path, params=params)
            return _ok(_get_gh()._check(resp, "workflow runs"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_run(owner: str, repo: str, run_id: int) -> str:
        """Get workflow run."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/actions/runs/{run_id}")
            return _ok(_get_gh()._check(resp, "workflow run"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_trigger_workflow(owner: str, repo: str, workflow_id: str, ref: str = "main", inputs: str = "") -> str:
        """Trigger workflow dispatch."""
        try:
            body: dict[str, Any] = {"ref": ref}
            if inputs: body["inputs"] = json.loads(inputs)
            resp = await _get_gh().post(f"repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches", json_body=body)
            if resp.status_code == 204:
                return _ok({"status": "triggered", "workflow": workflow_id})
            return _ok(_get_gh()._check(resp, "trigger"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_job_logs(owner: str, repo: str, job_id: int) -> str:
        """Get workflow job logs."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/actions/jobs/{job_id}/logs")
            return resp.text[:50000]
        except GitHubAPIError as e:
            return _err(str(e))

    # ══════════════════════════════════════════════════════════════════
    # SECURITY ADVISORIES
    # ══════════════════════════════════════════════════════════════════

    @server.tool()
    async def sassy_gh_list_global_advisories(ecosystem: str = "", severity: str = "", page: int = 1, per_page: int = 20) -> str:
        """List global security advisories."""
        try:
            params: dict[str, Any] = {"page": page, "per_page": per_page}
            if ecosystem: params["ecosystem"] = ecosystem
            if severity: params["severity"] = severity
            resp = await _get_gh().get("advisories", params=params)
            return _ok(_get_gh()._check(resp, "advisories"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_advisory(ghsa_id: str) -> str:
        """Get global advisory by GHSA ID."""
        try:
            resp = await _get_gh().get(f"advisories/{ghsa_id}")
            return _ok(_get_gh()._check(resp, "advisory"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_list_repo_advisories(owner: str, repo: str, state: str = "", page: int = 1) -> str:
        """List repo security advisories."""
        try:
            params: dict[str, Any] = {"page": page}
            if state: params["state"] = state
            resp = await _get_gh().get(f"repos/{owner}/{repo}/security-advisories", params=params)
            return _ok(_get_gh()._check(resp, "repo advisories"))
        except GitHubAPIError as e:
            return _err(str(e))

    # ══════════════════════════════════════════════════════════════════
    # GISTS
    # ══════════════════════════════════════════════════════════════════

    @server.tool()
    async def sassy_gh_list_gists(username: str = "", page: int = 1, per_page: int = 30) -> str:
        """List gists. Empty username = authenticated user."""
        try:
            path = f"users/{username}/gists" if username else "gists"
            resp = await _get_gh().get(path, params={"page": page, "per_page": per_page})
            return _ok(_get_gh()._check(resp, "gists"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_gist(gist_id: str) -> str:
        """Get a gist."""
        try:
            resp = await _get_gh().get(f"gists/{gist_id}")
            return _ok(_get_gh()._check(resp, "gist"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_create_gist(files: str, description: str = "", public: bool = False) -> str:
        """Create gist. files: JSON {"filename": {"content": "..."}}."""
        try:
            file_data = json.loads(files) if isinstance(files, str) else files
            body: dict[str, Any] = {"files": file_data, "public": public}
            if description: body["description"] = description
            resp = await _get_gh().post("gists", json_body=body)
            return _ok(_get_gh()._check(resp, "create gist"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_update_gist(gist_id: str, files: str, description: str = "") -> str:
        """Update gist. files: JSON {"filename": {"content": "..."}}."""
        try:
            file_data = json.loads(files) if isinstance(files, str) else files
            body: dict[str, Any] = {"files": file_data}
            if description: body["description"] = description
            resp = await _get_gh().patch(f"gists/{gist_id}", json_body=body)
            return _ok(_get_gh()._check(resp, "update gist"))
        except GitHubAPIError as e:
            return _err(str(e))

    # ══════════════════════════════════════════════════════════════════
    # LABELS
    # ══════════════════════════════════════════════════════════════════

    @server.tool()
    async def sassy_gh_list_labels(owner: str, repo: str, page: int = 1, per_page: int = 100) -> str:
        """List labels."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/labels", params={"page": page, "per_page": per_page})
            return _ok(_get_gh()._check(resp, "labels"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_label(owner: str, repo: str, name: str) -> str:
        """Get a label."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/labels/{name}")
            return _ok(_get_gh()._check(resp, "label"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_create_label(owner: str, repo: str, name: str, color: str = "", description: str = "") -> str:
        """Create label. color: hex without #."""
        try:
            body: dict[str, Any] = {"name": name}
            if color: body["color"] = color.lstrip("#")
            if description: body["description"] = description
            resp = await _get_gh().post(f"repos/{owner}/{repo}/labels", json_body=body)
            return _ok(_get_gh()._check(resp, "create label"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_update_label(owner: str, repo: str, name: str, new_name: str = "", color: str = "", description: str = "") -> str:
        """Update a label."""
        try:
            body: dict[str, Any] = {}
            if new_name: body["new_name"] = new_name
            if color: body["color"] = color.lstrip("#")
            if description: body["description"] = description
            resp = await _get_gh().patch(f"repos/{owner}/{repo}/labels/{name}", json_body=body)
            return _ok(_get_gh()._check(resp, "update label"))
        except GitHubAPIError as e:
            return _err(str(e))

    # ══════════════════════════════════════════════════════════════════
    # PROJECTS v2 (GraphQL)
    # ══════════════════════════════════════════════════════════════════

    @server.tool()
    async def sassy_gh_list_projects(owner: str, is_org: bool = False, per_page: int = 20) -> str:
        """List projects v2."""
        try:
            t = "organization" if is_org else "user"
            q = f"query($o:String!,$n:Int!){{{t}(login:$o){{projectsV2(first:$n){{nodes{{id number title url closed}}}}}}}}"
            resp = await _get_gh().post("graphql", json_body={"query": q, "variables": {"o": owner, "n": per_page}})
            data = _get_gh()._check(resp, "projects")
            return _ok(data.get("data", {}).get(t, {}).get("projectsV2", {}).get("nodes", []))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_project(project_number: int, owner: str, is_org: bool = False) -> str:
        """Get project by number."""
        try:
            t = "organization" if is_org else "user"
            q = f"query($o:String!,$n:Int!){{{t}(login:$o){{projectV2(number:$n){{id number title url closed shortDescription items(first:50){{nodes{{id content{{...on Issue{{title number}}...on PullRequest{{title number}}...on DraftIssue{{title}}}}}}}}}}}}}}"
            resp = await _get_gh().post("graphql", json_body={"query": q, "variables": {"o": owner, "n": project_number}})
            data = _get_gh()._check(resp, "project")
            return _ok(data.get("data", {}).get(t, {}).get("projectV2", {}))
        except GitHubAPIError as e:
            return _err(str(e))

    # ══════════════════════════════════════════════════════════════════
    # BRANCH PROTECTION (bonus - not in official MCP)
    # ══════════════════════════════════════════════════════════════════

    @server.tool()
    async def sassy_gh_protect_branch(owner: str, repo: str, branch: str = "main", require_pr: bool = False, required_approvals: int = 0, enforce_admins: bool = True, allow_force_push: bool = False, allow_deletions: bool = False) -> str:
        """Set branch protection rules."""
        try:
            body: dict[str, Any] = {"enforce_admins": enforce_admins, "required_status_checks": None, "restrictions": None, "allow_force_pushes": allow_force_push, "allow_deletions": allow_deletions}
            if require_pr or required_approvals > 0:
                body["required_pull_request_reviews"] = {"required_approving_review_count": max(required_approvals, 1), "dismiss_stale_reviews": True}
            else:
                body["required_pull_request_reviews"] = None
            resp = await _get_gh().put(f"repos/{owner}/{repo}/branches/{branch}/protection", json_body=body)
            return _ok(_get_gh()._check(resp, "protect branch"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_get_branch_protection(owner: str, repo: str, branch: str = "main") -> str:
        """Get branch protection rules."""
        try:
            resp = await _get_gh().get(f"repos/{owner}/{repo}/branches/{branch}/protection")
            return _ok(_get_gh()._check(resp, "branch protection"))
        except GitHubAPIError as e:
            return _err(str(e))

    @server.tool()
    async def sassy_gh_remove_branch_protection(owner: str, repo: str, branch: str = "main") -> str:
        """Remove branch protection."""
        try:
            await _get_gh().delete(f"repos/{owner}/{repo}/branches/{branch}/protection")
            return _ok({"status": "removed", "branch": branch})
        except GitHubAPIError as e:
            return _err(str(e))
