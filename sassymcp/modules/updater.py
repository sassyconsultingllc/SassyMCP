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
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from sassymcp import __version__
from sassymcp import _platform

logger = logging.getLogger("sassymcp.updater")

GITHUB_API = "https://api.github.com/repos/sassyconsultingllc/SassyMCP/releases"
PYPI_API = "https://pypi.org/pypi/sassymcp/json"
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


def _in_pipx() -> bool:
    """Detect whether the running sassymcp was installed via pipx."""
    # pipx installs into $PIPX_HOME/venvs/<pkg>/ or ~/.local/pipx/venvs/<pkg>/
    exe = os.path.realpath(sys.executable)
    return "pipx" in exe.lower() or "/pipx/" in exe


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

    def _check_pypi(self) -> dict[str, Any]:
        """Query PyPI for the latest version of sassymcp."""
        try:
            data = self._http_json(PYPI_API)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                json.JSONDecodeError) as e:
            return {"error": f"PyPI unreachable: {e}"}
        info = data.get("info", {})
        latest = info.get("version", "")
        return {
            "source": "pypi",
            "latest": latest,
            "current": __version__,
            "upgradable": _normalize(latest) > _normalize(__version__) if latest else False,
            "url": f"https://pypi.org/project/sassymcp/{latest}/",
            "summary": info.get("summary", ""),
        }

    def check_all_sources(self, force: bool = False) -> dict[str, Any]:
        """Check GitHub Releases and PyPI for the latest version.

        Returns a combined report showing what is available from each source
        and the recommended upgrade path.
        """
        gh = self.check(force=force)
        pypi = self._check_pypi()

        sources: list[dict[str, Any]] = []

        # GitHub Releases
        if "error" not in gh:
            sources.append({
                "source": "github",
                "latest": gh.get("latest"),
                "upgradable": gh.get("upgradable", False),
                "url": gh.get("release_url"),
                "published_at": gh.get("published_at"),
            })
        else:
            sources.append({"source": "github", "error": gh["error"]})

        # PyPI
        if "error" not in pypi:
            sources.append({
                "source": "pypi",
                "latest": pypi.get("latest"),
                "upgradable": pypi.get("upgradable", False),
                "url": pypi.get("url"),
            })
        else:
            sources.append({"source": "pypi", "error": pypi["error"]})

        # Determine the overall newest version across sources
        newest_ver = __version__
        newest_source = None
        for src in sources:
            ver = src.get("latest")
            if ver and _normalize(ver) > _normalize(newest_ver):
                newest_ver = ver
                newest_source = src["source"]

        any_upgrade = newest_source is not None

        # Suggest the right upgrade command based on install method
        upgrade_hint = ""
        if any_upgrade:
            import shutil
            if shutil.which("pipx") and _in_pipx():
                upgrade_hint = f"pipx upgrade sassymcp"
            elif shutil.which("pip"):
                upgrade_hint = f"pip install --upgrade sassymcp"
            else:
                upgrade_hint = f"pip install sassymcp=={newest_ver}"

        return {
            "current": __version__,
            "newest": newest_ver,
            "newest_source": newest_source,
            "upgrade_available": any_upgrade,
            "upgrade_hint": upgrade_hint,
            "sources": sources,
        }

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

    def check(self, force: bool = False, include_pypi: bool = True) -> dict[str, Any]:
        """Check for a newer SassyMCP release across GitHub and PyPI.

        Returns status from both sources and an overall upgradable flag.
        """
        state = self._refresh(force=force)
        if "error" in state:
            return state

        result: dict[str, Any] = {
            "current": state["current"],
            "latest": state["latest"],
            "upgradable": state["upgradable"],
            "release_url": state["release_url"],
            "published_at": state["latest_published_at"],
        }

        # Check PyPI alongside GitHub Releases
        if include_pypi:
            pypi = self._check_pypi()
            if "error" not in pypi:
                result["pypi_latest"] = pypi.get("latest")
                result["pypi_url"] = pypi.get("url")
                pypi_upgradable = pypi.get("upgradable", False)
                if pypi_upgradable:
                    result["upgradable"] = True
                    # If PyPI is newer than GitHub's latest, surface that
                    if not state.get("latest") or _normalize(pypi["latest"]) > _normalize(state["latest"]):
                        result["latest"] = pypi["latest"]
            else:
                result["pypi_error"] = pypi["error"]

        msg = (
            f"{result['current']} → {result['latest']} available"
            if result["upgradable"]
            else f"{result['current']} is the latest"
        )
        result["summary"] = msg

        # Add upgrade hint if an upgrade is available
        if result["upgradable"]:
            import shutil
            if shutil.which("pipx") and _in_pipx():
                result["upgrade_hint"] = "pipx upgrade sassymcp"
            elif shutil.which("pip"):
                result["upgrade_hint"] = "pip install --upgrade sassymcp"
            else:
                result["upgrade_hint"] = f"pip install sassymcp=={result['latest']}"

        return result

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

        require_checksum (default True): refuse to stage the asset unless a
        SHA-256 sidecar verifies. Missing sidecar is a hard fail under the
        default (v1.16.0+ publish policy). Set require_checksum=False only
        for an audited legacy release that never published checksums.
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
            checksum_status["status"] = "no_sidecar_published"
            if require_checksum:
                try:
                    dest.unlink()
                except OSError:
                    pass
                return {
                    "error": (
                        "No SHA-256 sidecar published for this release — "
                        "refusing to stage."
                    ),
                    "tag": info["tag"],
                    "asset": asset_name,
                    "checksum": checksum_status,
                    "hint": (
                        "v1.16.0+ requires SHA256SUMS or <asset>.sha256 on "
                        "the GitHub release. Pass require_checksum=False "
                        "only for an audited legacy release, or wait until "
                        "the sidecar is published."
                    ),
                }
            checksum_status["warning"] = (
                "No SHA-256 sidecar found. Staging anyway because "
                "require_checksum=False. Verify the download out-of-band "
                "before running."
            )

        name_lower = asset_name.lower()
        # Staging dir for extracted bundles (same on every host).
        extract_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SassyMCP" / info["tag"]
        if name_lower.endswith(".msi"):
            # Legacy MSI assets — kept working for older releases (<= v1.3.0).
            if _platform.IS_WINDOWS:
                run_cmd = f'msiexec /i "{dest}"'
                next_step = f"Run: {run_cmd}"
            else:
                # An MSI cannot install on POSIX — say so explicitly instead
                # of handing the user a Windows-only command (audit F-4).
                next_step = (
                    f"The .msi asset is Windows-only and cannot install on "
                    f"{_platform.OS_LABEL}. Download the standalone .exe "
                    f"(Windows) or use `pip install sassymcp` from release "
                    f"{info['tag']} instead."
                )
        elif name_lower.endswith((".tar.gz", ".tgz")):
            # tar preserves the executable bit — no chmod needed.
            run_cmd = f'tar -xzf "{dest}" -C "{extract_dir}"'
            next_step = f"Run: {run_cmd}"
        elif name_lower.endswith(".zip"):
            # Portable bundle — extract over the existing install dir.
            if _platform.IS_WINDOWS:
                run_cmd = f'Expand-Archive -Path "{dest}" -DestinationPath "{extract_dir}" -Force'
            else:
                # zip archives do not preserve the executable bit — re-add
                # it to the main binary after extracting (audit F-4).
                run_cmd = (f'unzip -o "{dest}" -d "{extract_dir}" && '
                           f'chmod +x "{extract_dir}/sassymcp"')
            next_step = f"Run: {run_cmd}"
        else:
            run_cmd = f'"{dest}"'
            next_step = f"Run: {run_cmd}"
        return {
            "tag": info["tag"],
            "asset": asset_name,
            "downloaded_to": str(dest),
            "size_bytes": dest.stat().st_size,
            "next_step": next_step,
            # The license-gated download mirror was retired (see the note above);
            # `license_key` no longer exists in this scope and referencing it
            # raised NameError on every successful download. Always False now.
            # The key is kept rather than dropped so existing callers that read
            # it keep working.
            "via_gated_url": False,
            "checksum": checksum_status,
        }


def register(server) -> None:
    import sys

    upd = Updater()
    _frozen = getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")

    @server.tool()
    def sassy_update_check(force: bool = False) -> dict:
        """Check for a newer SassyMCP release across GitHub Releases and PyPI.

        Caches the result for 5 minutes; pass force=True to bypass the cache.
        Returns the newest version found, source breakdown, and the upgrade
        command suited to the running install.
        """
        return upd.check(force=force)

    @server.tool()
    def sassy_update_list(tag: str | None = None) -> dict:
        """List downloadable assets for a release (apt-list-upgradable equivalent).

        tag defaults to the latest release.
        """
        return upd.list_assets(tag=tag)

    @server.tool()
    def sassy_update_changelog(tag: str | None = None) -> dict:
        """Return the release notes body for a tag (defaults to latest)."""
        return upd.changelog(tag=tag)

    @server.tool()
    def sassy_update_apply(asset_name: str, tag: str | None = None, dest_dir: str | None = None) -> dict:
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
