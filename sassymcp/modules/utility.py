"""Utility - Environment variables, notifications, archives, diffs, HTTP requests.

Lightweight tools that fill gaps between the heavier modules.
No external dependencies beyond stdlib + httpx (optional).
"""

import asyncio
import difflib
import json
import os
import shutil
import zipfile
from pathlib import Path


def register(server):

    # ── Environment Variables ─────────────────────────────────────────

    @server.tool()
    async def sassy_env_get(name: str) -> str:
        """Get an environment variable value. Returns error if not set."""
        val = os.environ.get(name) or os.getenv(name)
        if val is None:
            # Fallback: try reading from shell (catches inherited vars not in os.environ)
            try:
                proc = await asyncio.create_subprocess_exec(
                    "cmd.exe", "/c", f"echo %{name}%",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                shell_val = stdout.decode("utf-8", errors="replace").strip()
                if shell_val and shell_val != f"%{name}%":
                    val = shell_val
            except Exception:
                pass
        if val is None:
            return json.dumps({"error": f"'{name}' not set"})
        # Mask anything that looks like a token/key (show first 4 + last 4 chars)
        if any(kw in name.lower() for kw in ("token", "key", "secret", "password", "api")):
            if len(val) > 12:
                masked = val[:4] + "..." + val[-4:]
            else:
                masked = "****"
            return json.dumps({"name": name, "value": masked, "note": "masked for security, full value available to tools"})
        return json.dumps({"name": name, "value": val})

    @server.tool()
    async def sassy_env_set(name: str, value: str) -> str:
        """Set an environment variable for the current SassyMCP process.

        Persists for the lifetime of the server. Does NOT modify system env.
        For permanent changes, use the registry or system settings.
        """
        os.environ[name] = value
        return json.dumps({"set": name, "scope": "process", "note": "Effective until server restart"})

    @server.tool()
    async def sassy_env_list(filter_str: str = "") -> str:
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
        return json.dumps({"count": len(results), "variables": results}, indent=2)

    # ── Windows Toast Notifications ───────────────────────────────────

    @server.tool()
    async def sassy_toast(title: str, message: str, duration: str = "short") -> str:
        """Show a Windows toast notification. duration: short or long.

        Useful for alerting when a long-running task completes.
        Falls back to a BurntToast PowerShell module, then to msg.exe.
        """
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
                return json.dumps({"status": "sent", "method": "BurntToast", "title": title})
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass

        # Fallback: PowerShell .NET toast
        dur_ms = "7000" if duration == "short" else "25000"
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
            _, stderr2 = await asyncio.wait_for(proc2.communicate(), timeout=10)
            if proc2.returncode == 0:
                return json.dumps({"status": "sent", "method": ".NET Toast", "title": title})
        except asyncio.TimeoutError:
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
            return json.dumps({"status": "sent", "method": "msg.exe", "title": title})
        except asyncio.TimeoutError:
            try:
                proc3.kill()
            except Exception:
                pass
            return json.dumps({"status": "failed", "error": "All notification methods failed"})

    # ── Archive Operations ────────────────────────────────────────────

    @server.tool()
    async def sassy_zip(source: str, output: str = "", compression: str = "deflated") -> str:
        """Create a zip archive from a file or directory.

        source: path to file or directory to zip
        output: output zip path (defaults to source + .zip)
        compression: deflated (default), stored (no compression), bzip2, lzma
        """
        p = Path(source)
        if not p.exists():
            return json.dumps({"error": f"{source} does not exist"})

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
        with zipfile.ZipFile(output, "w", compression=comp) as zf:
            if p.is_file():
                zf.write(p, p.name)
                count = 1
                total_size = p.stat().st_size
            else:
                for root, dirs, files in os.walk(p):
                    for f in files:
                        fp = Path(root) / f
                        arcname = fp.relative_to(p)
                        zf.write(fp, arcname)
                        count += 1
                        total_size += fp.stat().st_size

        zip_size = Path(output).stat().st_size
        ratio = round((1 - zip_size / max(total_size, 1)) * 100, 1)
        return json.dumps({
            "created": output,
            "files": count,
            "original_bytes": total_size,
            "zip_bytes": zip_size,
            "compression_ratio": f"{ratio}%",
        })

    @server.tool()
    async def sassy_unzip(archive: str, destination: str = "", password: str = "") -> str:
        """Extract a zip archive.

        archive: path to .zip file
        destination: extract to this directory (defaults to archive parent)
        password: for encrypted zips
        """
        p = Path(archive)
        if not p.exists():
            return json.dumps({"error": f"{archive} does not exist"})

        if not destination:
            destination = str(p.parent / p.stem)

        try:
            pwd = password.encode() if password else None
            dest_resolved = os.path.realpath(destination)
            with zipfile.ZipFile(p, "r") as zf:
                # Zip-slip protection: validate all entry paths before extraction
                for member in zf.namelist():
                    member_path = os.path.realpath(os.path.join(destination, member))
                    if not member_path.startswith(dest_resolved):
                        return json.dumps({"error": f"Zip-slip detected: {member} escapes destination"})
                zf.extractall(destination, pwd=pwd)
                names = zf.namelist()
            return json.dumps({
                "extracted_to": destination,
                "files": len(names),
                "sample": names[:20],
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_tar(source: str, output: str = "", compress: str = "gz") -> str:
        """Create a tar archive. compress: gz, bz2, xz, or none."""
        import tarfile

        p = Path(source)
        if not p.exists():
            return json.dumps({"error": f"{source} does not exist"})

        ext_map = {"gz": ".tar.gz", "bz2": ".tar.bz2", "xz": ".tar.xz", "none": ".tar"}
        mode_map = {"gz": "w:gz", "bz2": "w:bz2", "xz": "w:xz", "none": "w"}

        if compress not in ext_map:
            return json.dumps({"error": f"Unknown compression: {compress}. Use: gz, bz2, xz, none"})

        if not output:
            output = str(p) + ext_map[compress]

        count = 0
        with tarfile.open(output, mode_map[compress]) as tf:
            tf.add(p, arcname=p.name)
            # Count members
            for _, _, files in os.walk(p) if p.is_dir() else [(None, None, [p.name])]:
                count += len(files) if files else 1

        return json.dumps({
            "created": output,
            "files": count,
            "size_bytes": Path(output).stat().st_size,
        })

    @server.tool()
    async def sassy_untar(archive: str, destination: str = "") -> str:
        """Extract a tar/tar.gz/tar.bz2/tar.xz archive."""
        import tarfile

        p = Path(archive)
        if not p.exists():
            return json.dumps({"error": f"{archive} does not exist"})

        if not destination:
            destination = str(p.parent / p.stem.replace(".tar", ""))

        try:
            with tarfile.open(p, "r:*") as tf:
                tf.extractall(destination, filter="data")
                names = tf.getnames()
            return json.dumps({
                "extracted_to": destination,
                "files": len(names),
                "sample": names[:20],
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    # ── File Diff ─────────────────────────────────────────────────────

    @server.tool()
    async def sassy_diff(path_a: str, path_b: str, context_lines: int = 3) -> str:
        """Compare two files and return a unified diff.

        context_lines: number of surrounding lines to show (default 3).
        Returns the diff text plus stats (added, removed, changed lines).
        """
        pa, pb = Path(path_a), Path(path_b)
        if not pa.exists():
            return json.dumps({"error": f"{path_a} does not exist"})
        if not pb.exists():
            return json.dumps({"error": f"{path_b} does not exist"})

        try:
            lines_a = pa.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            lines_b = pb.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        except Exception as e:
            return json.dumps({"error": str(e)})

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

        return json.dumps({
            "files": [path_a, path_b],
            "identical": len(diff) == 0,
            "lines_added": added,
            "lines_removed": removed,
            "diff": diff_text if diff else "(files are identical)",
        })

    # ── HTTP Requests ─────────────────────────────────────────────────

    @server.tool()
    async def sassy_http(url: str, method: str = "GET", headers: str = "", body: str = "", timeout_seconds: int = 15) -> str:
        """Make an HTTP request. Lightweight alternative to web_inspector for quick API calls.

        method: GET, POST, PUT, PATCH, DELETE, HEAD
        headers: JSON object of headers, e.g. {"Authorization": "Bearer xxx"}
        body: request body (string or JSON)
        """
        from sassymcp.modules._security import validate_url
        ok, err = validate_url(url)
        if not ok:
            return json.dumps({"error": err})

        try:
            import httpx
        except ImportError:
            # Fallback to urllib
            import urllib.request
            import urllib.error
            try:
                hdrs = json.loads(headers) if headers else {}
                req = urllib.request.Request(url, method=method.upper())
                for k, v in hdrs.items():
                    req.add_header(k, v)
                data = body.encode("utf-8") if body else None
                resp = urllib.request.urlopen(req, data=data, timeout=timeout_seconds)
                resp_body = resp.read().decode("utf-8", errors="replace")
                return json.dumps({
                    "status": resp.status,
                    "headers": dict(resp.headers),
                    "body": resp_body[:10000],
                    "method": method.upper(),
                    "url": url,
                })
            except urllib.error.HTTPError as e:
                return json.dumps({"status": e.code, "error": e.reason, "body": e.read().decode("utf-8", errors="replace")[:5000]})
            except Exception as e:
                return json.dumps({"error": str(e)})

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

            return json.dumps({
                "status": resp.status_code,
                "headers": dict(resp.headers),
                "body": resp_body,
                "method": method.upper(),
                "url": url,
            }, indent=2, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_http_ping(urls: str) -> str:
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
                try:
                    start = _time.monotonic()
                    req = urllib.request.Request(url, method="HEAD")
                    resp = urllib.request.urlopen(req, timeout=5)
                    elapsed = round((_time.monotonic() - start) * 1000)
                    results.append({"url": url, "status": resp.status, "ms": elapsed})
                except Exception as e:
                    results.append({"url": url, "status": "error", "error": str(e)})

        return json.dumps({"results": results, "count": len(results)}, indent=2)
