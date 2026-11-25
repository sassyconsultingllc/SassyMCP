# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-PS3DVUYPXIQT
"""Utility - Environment variables, notifications, archives, diffs, HTTP requests.

Lightweight tools that fill gaps between the heavier modules.
No external dependencies beyond stdlib + httpx (optional).
"""

import asyncio
import difflib
import json
import os
import zipfile
from pathlib import Path
from typing import Any

from sassymcp import _platform
from sassymcp.modules import audit as _audit
from sassymcp.modules._security import (
    is_sensitive_read_path,
    validate_url as _validate_url,
)


# ── Sensitive-read safety floor ─────────────────────────────────────
#
# is_sensitive_read_path() is the denylist for credential/secret material
# (~/.ssh, ~/.aws, SassyMCP tokens, /etc/shadow, browser login DBs, ...).
# It is a SAFETY FLOOR, not a permission rule: it runs before any
# permission-mode / allowlist logic and before the file is touched, so it
# holds in bypass, standard, and sandbox modes alike. Every tool below that
# surfaces file bytes to the MCP client calls _refuse_sensitive_read()
# first; the refusal is audit-logged and reaches the client as an error.

_SENSITIVE_READ_LABELS = (
    (".ssh", "SSH private key material"),
    (".gnupg", "PGP/GPG key material"),
    (".aws", "AWS credential file"),
    (".kube", "Kubernetes credential file"),
    (".docker", "Docker credential file"),
    (".netrc", "stored credential file"),
    (".pypirc", "stored credential file"),
    (".npmrc", "stored credential file"),
    ("tokens", "SassyMCP token/credential file"),
    ("license", "SassyMCP license file"),
    ("login data", "browser credential database"),
    ("cookies", "browser cookie store"),
    ("keychains", "OS keychain"),
    ("credentials", "OS credential vault"),
    ("profiles", "browser profile data"),
    ("shadow", "OS password hash store"),
    ("gshadow", "OS password hash store"),
    ("sudoers", "OS privilege configuration"),
    ("sam", "Windows SAM credential hive"),
    ("security", "Windows SECURITY credential hive"),
)


def _sensitive_read_label(reason: str) -> str:
    """Best-effort human class name for a sensitive-read denylist match."""
    lowered = reason.lower()
    for fragment, label in _SENSITIVE_READ_LABELS:
        if fragment in lowered:
            return label
    return "sensitive credential material"


def _refuse_sensitive_read(tool_name: str, path: str) -> str | None:
    """Safety-floor refusal for file-content reads of credential material.

    Runs BEFORE any permission-mode / allowlist logic and before the file
    is touched (no existence probe, no bytes read), so it holds in bypass,
    standard, and sandbox modes alike. Every refusal attempt is written to
    the audit log. Returns the client-facing refusal string, or None when
    the path is not on the denylist.
    """
    try:
        denied, reason = is_sensitive_read_path(path)
    except Exception as e:  # the floor must never crash a tool
        _audit.log_intercept(tool_name, "sensitive_read_check_error",
                             str(path), [str(path)], [repr(e)])
        return None
    if not denied:
        return None
    label = _sensitive_read_label(reason or "")
    _audit.log_intercept(tool_name, "sensitive_read_refused",
                         str(path), [str(path)], [reason or label])
    return f"Refused: will not read {label} ({path}): {reason}"


def _warn_sensitive_archive(tool_name: str, archive_path: str,
                            member_paths: list) -> str | None:
    """Warn+log when an archive includes sensitive-read denylist members.

    Unlike content reads, archive creation is NOT refused: blocking
    whole-directory archives would break legitimate full-directory
    backups. Members matching is_sensitive_read_path() are audit-logged
    (event 'sensitive_archive_warned') and the caller includes a
    'warning' field in its result. Returns the client-facing warning
    string, or None when no member matched. Must never crash a tool.
    """
    try:
        matched = [str(m) for m in member_paths if is_sensitive_read_path(m)[0]]
    except Exception as e:  # the floor must never crash a tool
        _audit.log_intercept(tool_name, "sensitive_archive_check_error",
                             str(archive_path), [str(archive_path)], [repr(e)])
        return None
    if not matched:
        return None
    _audit.log_intercept(tool_name, "sensitive_archive_warned",
                         str(archive_path), matched,
                         [f"{len(matched)} sensitive member(s) included in archive"])
    return ("Warning: archive includes {} sensitive path(s) matching the "
            "sensitive-read denylist: {}".format(len(matched), ", ".join(matched)))


def register(server):

    # ── Environment Variables ─────────────────────────────────────────

    @server.tool()
    def sassy_env_get(name: str) -> dict[str, Any]:
        """Get an environment variable value. Returns error if not set."""
        val = os.environ.get(name)
        if val is None:
            return {"error": f"'{name}' not set"}
        # Mask anything that looks like a token/key (show first 4 + last 4 chars)
        if any(kw in name.lower() for kw in ("token", "key", "secret", "password", "api")):
            if len(val) > 12:
                masked = val[:4] + "..." + val[-4:]
            else:
                masked = "****"
            return {"name": name, "value": masked, "note": "masked for security, full value available to tools"}
        return {"name": name, "value": val}

    @server.tool()
    def sassy_env_set(name: str, value: str) -> dict[str, Any]:
        """Set an environment variable for the current SassyMCP process.

        Persists for the lifetime of the server. Does NOT modify system env.
        For permanent changes, use the registry or system settings.
        """
        os.environ[name] = value
        return {"set": name, "scope": "process", "note": "Effective until server restart"}

    @server.tool()
    def sassy_env_list(filter_str: str = "") -> dict[str, Any]:
        """List environment variables. Optional filter by name substring.
        Sensitive values (tokens, keys, secrets) are masked."""
        sensitive = ("token", "key", "secret", "password", "api", "credential")
        results = {}
        for k, v in sorted(os.environ.items()):
            if filter_str and filter_str.lower() not in k.lower():
                continue
            if any(s in k.lower() for s in sensitive):
                results[k] = v[:4] + "..." + v[-4:] if len(v) > 12 else "****"
            else:
                results[k] = v[:200] + "..." if len(v) > 200 else v
        return {"count": len(results), "variables": results}

    # ── Windows Toast Notifications ───────────────────────────────────

    @server.tool()
    async def sassy_toast(title: str, message: str, duration: str = "short") -> dict[str, Any]:
        """Show a desktop notification. duration: short or long.

        Useful for alerting when a long-running task completes. Routed at the
        head: Windows toast (BurntToast -> .NET -> msg.exe), macOS osascript
        notification, Linux notify-send.
        """
        if duration not in ("short", "long"):
            duration = "short"

        # ── macOS / Linux native notifications ────────────────────────
        if not _platform.IS_WINDOWS:
            if _platform.IS_MACOS:
                # Pass title/message as AppleScript run-args so no escaping is
                # needed regardless of quotes/newlines in the content.
                argv = ["osascript",
                        "-e", "on run argv",
                        "-e", "display notification (item 1 of argv) with title (item 2 of argv)",
                        "-e", "end run",
                        message, title]
                method = "osascript"
            elif _platform.which("notify-send"):
                argv = ["notify-send",
                        "-u", "normal" if duration == "short" else "critical",
                        title, message]
                method = "notify-send"
            else:
                return {"status": "failed",
                                   "error": "No notifier found (install libnotify / notify-send)."}
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
                if proc.returncode == 0:
                    return {"status": "sent", "method": method, "title": title}
                return {"status": "failed", "method": method,
                                   "error": stderr.decode("utf-8", errors="replace").strip()}
            except TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                return {"status": "failed", "method": method, "error": "timed out"}

        # ── Windows toast ─────────────────────────────────────────────
        # Sanitize inputs for XML/PowerShell safety
        import xml.sax.saxutils
        safe_title = title.replace(chr(39), chr(39)+chr(39))  # PS single-quote escape
        safe_message = message.replace(chr(39), chr(39)+chr(39))
        xml_title = xml.sax.saxutils.escape(title)
        xml_message = xml.sax.saxutils.escape(message)

        # Try BurntToast first (most capable)
        ps_bt = f"New-BurntToastNotification -Text '{safe_title}', '{safe_message}'"
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-Command", ps_bt,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                return {"status": "sent", "method": "BurntToast", "title": title}
        except TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass

        # Fallback: PowerShell .NET toast.
        # (A `dur_ms` millisecond value was computed here and never used — the
        # toast XML takes the "short"/"long" keyword in its duration attribute,
        # which `duration` already supplies. Dead assignment removed.)
        ps_net = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null; "
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime] | Out-Null; "
            f"$xml = '<toast duration=\"{duration}\"><visual><binding template=\"ToastGeneric\">"
            f"<text>{xml_title}</text><text>{xml_message}</text></binding></visual></toast>'; "
            "$xdoc = [Windows.Data.Xml.Dom.XmlDocument]::new(); $xdoc.LoadXml($xml); "
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($xdoc); "
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('SassyMCP').Show($toast)"
        )
        proc2 = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-Command", ps_net,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            _, _stderr2 = await asyncio.wait_for(proc2.communicate(), timeout=10)
            if proc2.returncode == 0:
                return {"status": "sent", "method": ".NET Toast", "title": title}
        except TimeoutError:
            try:
                proc2.kill()
            except Exception:
                pass

        # Last resort: msg.exe to console
        proc3 = await asyncio.create_subprocess_exec(
            "msg.exe", "*", f"{title}: {message}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            await asyncio.wait_for(proc3.communicate(), timeout=5)
            return {"status": "sent", "method": "msg.exe", "title": title}
        except TimeoutError:
            try:
                proc3.kill()
            except Exception:
                pass
            return {"status": "failed", "error": "All notification methods failed"}

    # ── Archive Operations ────────────────────────────────────────────

    @server.tool()
    def sassy_zip(source: str, output: str = "", compression: str = "deflated") -> dict[str, Any]:
        """Create a zip archive from a file or directory.

        source: path to file or directory to zip
        output: output zip path (defaults to source + .zip)
        compression: deflated (default), stored (no compression), bzip2, lzma

        Sensitive members are NOT blocked (full-directory backups must keep
        working): if any archived file matches the sensitive-read denylist
        (SSH keys, credential stores, ...), the archive is still created but
        the result carries a 'warning' field and the event is audit-logged.
        """
        p = Path(source)
        if not p.exists():
            return {"error": f"{source} does not exist"}

        if not output:
            output = str(p.with_suffix(".zip")) if p.is_file() else str(p) + ".zip"

        comp_map = {
            "deflated": zipfile.ZIP_DEFLATED,
            "stored": zipfile.ZIP_STORED,
            "bzip2": zipfile.ZIP_BZIP2,
            "lzma": zipfile.ZIP_LZMA,
        }
        comp = comp_map.get(compression, zipfile.ZIP_DEFLATED)

        count = 0
        total_size = 0
        member_paths: list[str] = []
        with zipfile.ZipFile(output, "w", compression=comp) as zf:
            if p.is_file():
                zf.write(p, p.name)
                count = 1
                total_size = p.stat().st_size
                member_paths.append(str(p))
            else:
                for root, dirs, files in os.walk(p):
                    for f in files:
                        fp = Path(root) / f
                        arcname = fp.relative_to(p)
                        zf.write(fp, arcname)
                        count += 1
                        total_size += fp.stat().st_size
                        member_paths.append(str(fp))

        zip_size = Path(output).stat().st_size
        ratio = round((1 - zip_size / max(total_size, 1)) * 100, 1)
        result: dict[str, Any] = {
            "created": output,
            "files": count,
            "original_bytes": total_size,
            "zip_bytes": zip_size,
            "compression_ratio": f"{ratio}%",
        }
        # Warn+log (do NOT refuse): members matching the sensitive-read
        # denylist are still archived so legitimate full-directory backups
        # keep working, but the inclusion is flagged to the caller and
        # audit-logged.
        warning = _warn_sensitive_archive("sassy_zip", output, member_paths)
        if warning:
            result["warning"] = warning
        return result

    @server.tool()
    def sassy_unzip(archive: str, destination: str = "", password: str = "") -> dict[str, Any]:
        """Extract a zip archive.

        archive: path to .zip file
        destination: extract to this directory (defaults to archive parent)
        password: for encrypted zips
        """
        p = Path(archive)
        if not p.exists():
            return {"error": f"{archive} does not exist"}

        if not destination:
            destination = str(p.parent / p.stem)

        try:
            pwd = password.encode() if password else None
            dest_resolved = os.path.realpath(destination)
            with zipfile.ZipFile(p, "r") as zf:
                # Zip-slip protection: validate all entry paths before
                # extraction. Compare with os.path.commonpath(), NOT a bare
                # str.startswith(): a sibling-prefix collision (destination
                # /tmp/x, member ../xevil/pwned -> /tmp/xevil/pwned) passes
                # a startswith check while still escaping the destination.
                for member in zf.namelist():
                    member_path = os.path.realpath(os.path.join(destination, member))
                    try:
                        inside = os.path.commonpath([dest_resolved, member_path]) == dest_resolved
                    except ValueError:
                        inside = False  # e.g. different drive letters on Windows
                    if not inside:
                        return {"error": f"Zip-slip detected: {member} escapes destination"}
                zf.extractall(destination, pwd=pwd)
                names = zf.namelist()
            return {
                "extracted_to": destination,
                "files": len(names),
                "sample": names[:20],
            }
        except Exception as e:
            return {"error": str(e)}

    @server.tool()
    def sassy_tar(source: str, output: str = "", compress: str = "gz") -> dict[str, Any]:
        """Create a tar archive. compress: gz, bz2, xz, or none.

        Sensitive members are NOT blocked (full-directory backups must keep
        working): if any archived file matches the sensitive-read denylist
        (SSH keys, credential stores, ...), the archive is still created but
        the result carries a 'warning' field and the event is audit-logged.
        """
        import tarfile

        p = Path(source)
        if not p.exists():
            return {"error": f"{source} does not exist"}

        ext_map = {"gz": ".tar.gz", "bz2": ".tar.bz2", "xz": ".tar.xz", "none": ".tar"}
        mode_map = {"gz": "w:gz", "bz2": "w:bz2", "xz": "w:xz", "none": "w"}

        if compress not in ext_map:
            return {"error": f"Unknown compression: {compress}. Use: gz, bz2, xz, none"}

        if not output:
            output = str(p) + ext_map[compress]

        # Collect member paths for the sensitive-member scan. count matches
        # the previous walk-based tally (files only, directories excluded).
        member_paths: list[str] = []
        if p.is_dir():
            for root, _dirs, files in os.walk(p):
                for f in files:
                    member_paths.append(str(Path(root) / f))
        else:
            member_paths = [str(p)]
        count = len(member_paths)

        with tarfile.open(output, mode_map[compress]) as tf:
            tf.add(p, arcname=p.name)

        result: dict[str, Any] = {
            "created": output,
            "files": count,
            "size_bytes": Path(output).stat().st_size,
        }
        # Warn+log (do NOT refuse): members matching the sensitive-read
        # denylist are still archived so legitimate full-directory backups
        # keep working, but the inclusion is flagged to the caller and
        # audit-logged.
        warning = _warn_sensitive_archive("sassy_tar", output, member_paths)
        if warning:
            result["warning"] = warning
        return result

    @server.tool()
    def sassy_untar(archive: str, destination: str = "") -> dict[str, Any]:
        """Extract a tar/tar.gz/tar.bz2/tar.xz archive."""
        import tarfile

        p = Path(archive)
        if not p.exists():
            return {"error": f"{archive} does not exist"}

        if not destination:
            destination = str(p.parent / p.stem.replace(".tar", ""))

        try:
            with tarfile.open(p, "r:*") as tf:
                tf.extractall(destination, filter="data")
                names = tf.getnames()
            return {
                "extracted_to": destination,
                "files": len(names),
                "sample": names[:20],
            }
        except Exception as e:
            return {"error": str(e)}

    # ── File Diff ─────────────────────────────────────────────────────

    @server.tool()
    def sassy_diff(path_a: str, path_b: str, context_lines: int = 3) -> dict[str, Any]:
        """Compare two files and return a unified diff.

        context_lines: number of surrounding lines to show (default 3).
        Returns the diff text plus stats (added, removed, changed lines).
        """
        # Safety floor: refuse credential/secret material before touching
        # either file, regardless of permission mode.
        for _which, _pth in (("path_a", path_a), ("path_b", path_b)):
            _refusal = _refuse_sensitive_read("sassy_diff", _pth)
            if _refusal:
                return {"error": _refusal}
        pa, pb = Path(path_a), Path(path_b)
        if not pa.exists():
            return {"error": f"{path_a} does not exist"}
        if not pb.exists():
            return {"error": f"{path_b} does not exist"}

        try:
            lines_a = pa.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            lines_b = pb.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        except Exception as e:
            return {"error": str(e)}

        diff = list(difflib.unified_diff(
            lines_a, lines_b,
            fromfile=path_a, tofile=path_b,
            n=context_lines,
        ))

        added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))

        diff_text = "".join(diff)
        if len(diff_text) > 20000:
            diff_text = diff_text[:20000] + "\n...(truncated)"

        return {
            "files": [path_a, path_b],
            "identical": len(diff) == 0,
            "lines_added": added,
            "lines_removed": removed,
            "diff": diff_text if diff else "(files are identical)",
        }

    # ── HTTP Requests ─────────────────────────────────────────────────

    @server.tool()
    async def sassy_http(url: str, method: str = "GET", headers: str = "", body: str = "", timeout_seconds: int = 15, allow_mutating: bool = False) -> dict[str, Any]:
        """Make an HTTP request. Lightweight alternative to web_inspector for quick API calls.

        method: GET, HEAD, OPTIONS, POST, PUT, PATCH, DELETE
        headers: JSON object of headers, e.g. {"Authorization": "Bearer xxx"}
        body: request body (string or JSON)
        allow_mutating: required True for state-changing verbs (POST, PUT,
          PATCH, DELETE). The MCP tool surface is reachable by an LLM that
          may be partially adversarial; gating mutating verbs behind an
          explicit flag stops an "innocent fetch" prompt from being steered
          into a DELETE against an internal service.
        """
        from sassymcp.modules._security import validate_url
        ok, err = validate_url(url)
        if not ok:
            return {"error": err}

        method_upper = method.upper().strip()
        _READ_VERBS = {"GET", "HEAD", "OPTIONS"}
        _ALL_VERBS = _READ_VERBS | {"POST", "PUT", "PATCH", "DELETE"}
        if method_upper not in _ALL_VERBS:
            return {"error": f"Unsupported HTTP method: {method!r}"}
        if method_upper not in _READ_VERBS and not allow_mutating:
            return {
                "error": (
                    f"{method_upper} requires allow_mutating=True. Read-only "
                    "verbs (GET / HEAD / OPTIONS) run without the flag."
                ),
                "method": method_upper,
            }

        try:
            import httpx
        except ImportError:
            # Fallback to urllib
            import urllib.error
            import urllib.request
            try:
                hdrs = json.loads(headers) if headers else {}
                req = urllib.request.Request(url, method=method.upper())
                for k, v in hdrs.items():
                    req.add_header(k, v)
                data = body.encode("utf-8") if body else None

                # urllib is fully blocking and this tool is `async def`, so calling
                # it inline would stall the event loop for the whole request —
                # wedging every other session for up to `timeout_seconds`. Do the
                # connect AND the read on a worker thread.
                def _blocking_fetch() -> dict:
                    with urllib.request.urlopen(req, data=data, timeout=timeout_seconds) as resp:
                        return {
                            "status": resp.status,
                            "headers": dict(resp.headers),
                            "body": resp.read().decode("utf-8", errors="replace")[:10000],
                            "method": method.upper(),
                            "url": url,
                        }

                return await asyncio.to_thread(_blocking_fetch)
            except urllib.error.HTTPError as e:
                return {"status": e.code, "error": e.reason, "body": e.read().decode("utf-8", errors="replace")[:5000]}
            except Exception as e:
                return {"error": str(e)}

        try:
            hdrs = json.loads(headers) if headers else {}
            async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
                resp = await client.request(
                    method.upper(), url,
                    headers=hdrs,
                    content=body.encode("utf-8") if body else None,
                )
            # Try to parse response as JSON for cleaner output
            try:
                resp_json = resp.json()
                resp_body = resp_json
            except Exception:
                resp_body = resp.text[:10000]

            return json.loads(json.dumps({
                "status": resp.status_code,
                "headers": dict(resp.headers),
                "body": resp_body,
                "method": method.upper(),
                "url": url,
            }, default=str))
        except Exception as e:
            return {"error": str(e)}

    @server.tool()
    async def sassy_http_ping(urls: str) -> dict[str, Any]:
        """Quick health check on multiple URLs. Returns status code and response time for each.

        urls: comma-separated list of URLs to check.
        """
        import time as _time
        url_list = [u.strip() for u in urls.split(",") if u.strip()]
        results = []

        try:
            import httpx
            async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
                for url in url_list:
                    ok, err = _validate_url(url)
                    if not ok:
                        results.append({"url": url, "status": "blocked", "error": err})
                        continue
                    try:
                        start = _time.monotonic()
                        resp = await client.head(url)
                        elapsed = round((_time.monotonic() - start) * 1000)
                        results.append({"url": url, "status": resp.status_code, "ms": elapsed})
                    except Exception as e:
                        results.append({"url": url, "status": "error", "error": str(e)})
        except ImportError:
            import urllib.request
            for url in url_list:
                ok, err = _validate_url(url)
                if not ok:
                    results.append({"url": url, "status": "blocked", "error": err})
                    continue
                try:
                    start = _time.monotonic()
                    req = urllib.request.Request(url, method="HEAD")
                    # Blocking urlopen inside an async tool, and inside a loop:
                    # without the thread hop a list of N unreachable hosts would
                    # freeze the whole server for N x 5s.
                    resp = await asyncio.to_thread(urllib.request.urlopen, req, timeout=5)
                    elapsed = round((_time.monotonic() - start) * 1000)
                    results.append({"url": url, "status": resp.status, "ms": elapsed})
                except Exception as e:
                    results.append({"url": url, "status": "error", "error": str(e)})

        return {"results": results, "count": len(results)}
