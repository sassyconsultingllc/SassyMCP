# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-7RCCQFURONNW
"""SassyMCP Updater — Kali-style version checking and self-update.

Mirrors `apt update` / `apt list --upgradable` / `apt upgrade` semantics
against GitHub Releases (or the license-gated sassyconsultingllc.com
endpoint when SASSYMCP_LICENSE_KEY is set).

Tools:
    sassy_update_check       — like `apt update`: refresh remote state, return summary
    sassy_update_list        — like `apt list --upgradable`: detail rows per asset
    sassy_update_changelog   — release notes for a specific tag
    sassy_update_apply       — download asset to staging, print run instruction

Apply intentionally does NOT execute the installer. The MSI needs UAC; the
user runs it explicitly. This matches `apt`'s contract: download is automatic,
state-changing install is a deliberate command.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

from sassymcp import __version__

logger = logging.getLogger("sassymcp.updater")

GITHUB_API = "https://api.github.com/repos/sassyconsultingllc/SassyMCP/releases"
CACHE_TTL_SECONDS = 300
USER_AGENT = f"sassymcp/{__version__} (+https://github.com/sassyconsultingllc/SassyMCP)"


def _normalize(v: str) -> tuple[int, ...]:
    """Parse '1.2.0', 'v1.2.0', '1.2.0-dev' to a comparable tuple.

    Pre-release suffixes (-dev, -rc1, etc.) sort BEFORE the same release
    without a suffix (1.2.0-dev < 1.2.0), matching apt/semver intuition.
    """
    s = v.lstrip("vV")
    # Strip and remember the prerelease suffix
    pre = ""
    if "-" in s:
        s, pre = s.split("-", 1)
    parts = re.findall(r"\d+", s)
    nums = tuple(int(p) for p in parts) if parts else (0,)
    # 0 if pre-release, 1 if final release — final sorts higher
    pre_rank = 0 if pre else 1
    return nums + (pre_rank,)


class Updater:
    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}
        self._cache_ts: float = 0.0

    def _http_json(self, url: str, timeout: float = 10.0) -> Any:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _refresh(self, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force and self._cache and (now - self._cache_ts) < CACHE_TTL_SECONDS:
            return self._cache
        try:
            releases = self._http_json(GITHUB_API)
        except urllib.error.HTTPError as e:
            return {"error": f"GitHub API HTTP {e.code}: {e.reason}"}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            return {"error": f"GitHub API unreachable: {e}"}

        # Filter out drafts & sort newest-first by version tuple
        published = [r for r in releases if not r.get("draft")]
        published.sort(key=lambda r: _normalize(r.get("tag_name", "0.0.0")), reverse=True)

        latest = published[0] if published else None
        result = {
            "current": __version__,
            "latest": latest.get("tag_name") if latest else None,
            "latest_published_at": latest.get("published_at") if latest else None,
            "release_url": latest.get("html_url") if latest else None,
            "all_versions": [r.get("tag_name") for r in published[:20]],
            "_releases": published[:5],  # cached for downstream tools
        }
        if result["latest"]:
            result["upgradable"] = _normalize(result["latest"]) > _normalize(__version__)
        else:
            result["upgradable"] = False
        self._cache = result
        self._cache_ts = now
        return result

    def check(self, force: bool = False) -> dict[str, Any]:
        state = self._refresh(force=force)
        if "error" in state:
            return state
        msg = (
            f"{state['current']} → {state['latest']} available"
            if state["upgradable"]
            else f"{state['current']} is the latest"
        )
        return {
            "current": state["current"],
            "latest": state["latest"],
            "upgradable": state["upgradable"],
            "summary": msg,
            "release_url": state["release_url"],
            "published_at": state["latest_published_at"],
        }

    def list_assets(self, tag: str | None = None) -> dict[str, Any]:
        state = self._refresh()
        if "error" in state:
            return state
        target_tag = tag or state["latest"]
        rel = next((r for r in state["_releases"] if r.get("tag_name") == target_tag), None)
        if not rel:
            return {"error": f"No release found for tag {target_tag!r}"}
        assets = [
            {
                "name": a.get("name"),
                "size_bytes": a.get("size"),
                "download_url": a.get("browser_download_url"),
                "content_type": a.get("content_type"),
                "downloads": a.get("download_count"),
            }
            for a in rel.get("assets", [])
        ]
        return {
            "tag": target_tag,
            "current": __version__,
            "assets": assets,
            "asset_count": len(assets),
        }

    def changelog(self, tag: str | None = None) -> dict[str, Any]:
        state = self._refresh()
        if "error" in state:
            return state
        target_tag = tag or state["latest"]
        rel = next((r for r in state["_releases"] if r.get("tag_name") == target_tag), None)
        if not rel:
            return {"error": f"No release found for tag {target_tag!r}"}
        return {
            "tag": target_tag,
            "name": rel.get("name"),
            "published_at": rel.get("published_at"),
            "body": rel.get("body") or "(no release notes)",
            "url": rel.get("html_url"),
        }

    def _fetch_checksum(self, tag: str, asset_name: str) -> tuple[str | None, str | None]:
        """Fetch the expected SHA-256 for a release asset.

        Looks for one of these sidecars in the release's asset list, in
        priority order:
          1. <asset_name>.sha256   — single-line "<hex>  <name>"
          2. SHA256SUMS            — multi-line, GNU coreutils format
          3. checksums.txt         — same shape as SHA256SUMS

        Returns (hex_digest, source_filename) on hit, (None, None) if no
        sidecar is published. The caller decides whether absence is fatal.
        """
        info = self.list_assets(tag=tag)
        if "error" in info:
            return None, None
        assets = info.get("assets") or []
        # Per-asset .sha256 wins if present
        sidecar = next((a for a in assets if a["name"] == f"{asset_name}.sha256"), None)
        # Otherwise the aggregate file
        if sidecar is None:
            sidecar = next(
                (a for a in assets if a["name"] in ("SHA256SUMS", "checksums.txt", "SHA256SUMS.txt")),
                None,
            )
        if sidecar is None:
            return None, None
        url = sidecar["download_url"]
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError):
            return None, None
        # Parse "<hex>  <name>" lines and pick the row matching our asset
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if not parts:
                continue
            digest = parts[0].strip().lower()
            name = parts[1].strip().lstrip("*") if len(parts) > 1 else asset_name
            if len(digest) == 64 and (name == asset_name or sidecar["name"].endswith(".sha256")):
                return digest, sidecar["name"]
        return None, None

    def apply(
        self,
        asset_name: str,
        tag: str | None = None,
        dest_dir: str | None = None,
        require_checksum: bool = True,
    ) -> dict[str, Any]:
        """Download an asset to staging. Does NOT execute it.

        require_checksum (default True): refuse to return a run command if
        the release publishes a SHA-256 sidecar AND verification fails. If
        no sidecar is published, downgrades to a warning rather than
        refusing — many older releases predate sidecar publication. Set
        require_checksum=False to bypass entirely (e.g., when checksum
        sidecar is being introduced and old assets haven't been backfilled).
        """
        info = self.list_assets(tag=tag)
        if "error" in info:
            return info
        asset = next((a for a in info["assets"] if a["name"] == asset_name), None)
        if not asset:
            return {
                "error": f"Asset {asset_name!r} not found in release {info['tag']}",
                "available": [a["name"] for a in info["assets"]],
            }

        # One binary for everyone: the GitHub release asset is the free
        # build, and the license unlocks paid groups at runtime. (The old
        # license-gated download mirror is retired — there is no separate
        # paid binary to fetch.)
        url = asset["download_url"]

        dest_root = Path(dest_dir) if dest_dir else Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SassyMCP" / "updates"
        dest_root.mkdir(parents=True, exist_ok=True)
        dest = dest_root / asset_name

        # Stream + hash in one pass so we never have to re-read the file.
        import hashlib
        hasher = hashlib.sha256()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=300) as resp, open(dest, "wb") as fh:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
                    hasher.update(chunk)
        except (urllib.error.URLError, TimeoutError) as e:
            return {"error": f"Download failed: {e}", "url": url}
        actual_sha256 = hasher.hexdigest()

        expected_sha256, sidecar_name = self._fetch_checksum(info["tag"], asset_name)
        checksum_status: dict[str, Any] = {
            "sha256_actual": actual_sha256,
            "sha256_expected": expected_sha256,
            "sidecar": sidecar_name,
        }
        if expected_sha256:
            if expected_sha256.lower() != actual_sha256.lower():
                # Hard fail — refuse to return a run command for a tampered
                # download. Delete the bad file so it can't be invoked
                # accidentally.
                try:
                    dest.unlink()
                except OSError:
                    pass
                return {
                    "error": "Checksum mismatch — refusing to install.",
                    "tag": info["tag"],
                    "asset": asset_name,
                    "expected_sha256": expected_sha256,
                    "actual_sha256": actual_sha256,
                    "sidecar": sidecar_name,
                    "hint": (
                        "The published SHA-256 sidecar does not match the "
                        "downloaded bytes. This is either tampering, "
                        "corruption in transit, or a stale sidecar. Do NOT "
                        "run the asset. Investigate before retrying."
                    ),
                }
            checksum_status["status"] = "verified"
        else:
            # Sidecar not published — downgrade to warning unless caller
            # explicitly required a checksum.
            checksum_status["status"] = "no_sidecar_published"
            if require_checksum is True and sidecar_name is None:
                # Default policy: allow but flag loudly. This avoids
                # breaking older releases that don't yet publish sidecars.
                checksum_status["warning"] = (
                    "No SHA-256 sidecar found for this release. Verify the "
                    "download out-of-band before running. Future releases "
                    "should ship <asset>.sha256 or SHA256SUMS alongside."
                )

        name_lower = asset_name.lower()
        if name_lower.endswith(".msi"):
            # Legacy MSI assets — kept working for older releases (<= v1.3.0).
            run_cmd = f'msiexec /i "{dest}"'
        elif name_lower.endswith(".zip"):
            # Portable bundle — extract over the existing install dir.
            extract_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SassyMCP" / info["tag"]
            run_cmd = f'Expand-Archive -Path "{dest}" -DestinationPath "{extract_dir}" -Force'
        else:
            run_cmd = f'"{dest}"'
        return {
            "tag": info["tag"],
            "asset": asset_name,
            "downloaded_to": str(dest),
            "size_bytes": dest.stat().st_size,
            "next_step": f"Run: {run_cmd}",
            "via_gated_url": bool(license_key),
            "checksum": checksum_status,
        }


def register(server) -> None:
    import sys

    upd = Updater()
    _frozen = getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")

    @server.tool()
    async def sassy_update_check(force: bool = False) -> dict:
        """Check for a newer SassyMCP release (apt-update equivalent).

        Caches the result for 5 minutes; pass force=True to bypass the cache.
        """
        return upd.check(force=force)

    @server.tool()
    async def sassy_update_list(tag: str | None = None) -> dict:
        """List downloadable assets for a release (apt-list-upgradable equivalent).

        tag defaults to the latest release.
        """
        return upd.list_assets(tag=tag)

    @server.tool()
    async def sassy_update_changelog(tag: str | None = None) -> dict:
        """Return the release notes body for a tag (defaults to latest)."""
        return upd.changelog(tag=tag)

    @server.tool()
    async def sassy_update_apply(asset_name: str, tag: str | None = None, dest_dir: str | None = None) -> dict:
        """Download an asset to staging. Returns the path + a run command.

        Does NOT execute the installer — the user runs the returned command
        when ready. Disabled in packaged/frozen builds so marketplace installs
        remain a fixed graded version; upgrade by installing a new release
        artifact instead.
        """
        if _frozen:
            return {
                "error": "update_apply unavailable in packaged build",
                "reason": (
                    "This SassyMCP is a fixed graded install. Download a new "
                    "release .mcpb / exe from GitHub Releases instead of "
                    "in-place apply."
                ),
                "runtime": "frozen",
            }
        return upd.apply(asset_name=asset_name, tag=tag, dest_dir=dest_dir)

    server.updater = upd
    logger.info(
        "Updater module loaded%s",
        " (apply disabled in frozen build)" if _frozen else "",
    )
