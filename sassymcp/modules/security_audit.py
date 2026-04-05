"""SecurityAudit - Hash verification, permissions, certs, APK analysis, forensics."""

import asyncio
import hashlib
import json

def register(server):
    @server.tool()
    async def sassy_hash_file(path: str, algorithm: str = "sha256") -> str:
        """Compute file hash. algorithm: md5, sha1, sha256, sha512."""
        algos = {"md5": hashlib.md5, "sha1": hashlib.sha1, "sha256": hashlib.sha256, "sha512": hashlib.sha512}
        if algorithm not in algos: return f"Error: use {', '.join(algos)}"
        h = algos[algorithm]()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return f"{algorithm}: {h.hexdigest()}"

    @server.tool()
    async def sassy_file_permissions(path: str) -> str:
        """Check file/directory permissions (Windows ACLs)."""
        # Escape single quotes for PS single-quoted string
        safe_path = path.replace("'", "''")
        ps_script = f"Get-Acl '{safe_path}' | Format-List"
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-Command", ps_script,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        return stdout.decode("utf-8", errors="replace").strip()

    @server.tool()
    async def sassy_cert_check(target: str, port: int = 443) -> str:
        """Check TLS certificate for a host."""
        import re
        import ssl
        import socket
        if not re.match(r'^[A-Za-z0-9\.\-\:]+$', target):
            return f"Error: invalid target: {target!r}"
        # Use synchronous ssl socket — avoids Python 3.14 asyncio
        # APPLICATION_DATA_AFTER_CLOSE_NOTIFY errors
        ctx = ssl.create_default_context()
        try:
            def _get_cert():
                with socket.create_connection((target, port), timeout=10) as sock:
                    with ctx.wrap_socket(sock, server_hostname=target) as ssock:
                        return ssock.getpeercert()
            cert = await asyncio.get_event_loop().run_in_executor(None, _get_cert)
        except Exception as e:
            return json.dumps({"error": str(e)})
        if not cert:
            return json.dumps({"error": "could not retrieve certificate"})
        return json.dumps({
            "subject": dict(x[0] for x in cert.get("subject", ())),
            "issuer": dict(x[0] for x in cert.get("issuer", ())),
            "notBefore": cert.get("notBefore"), "notAfter": cert.get("notAfter"),
            "SAN": [e[1] for e in cert.get("subjectAltName", ())],
        }, indent=2)

    @server.tool()
    async def sassy_apk_info(apk_path: str) -> str:
        """Analyze APK: permissions, signatures, package info."""
        import shutil
        import zipfile
        aapt = shutil.which("aapt") or shutil.which("aapt2")
        if aapt:
            proc = await asyncio.create_subprocess_exec(
                aapt, "dump", "badging", apk_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            return stdout.decode("utf-8", errors="replace").strip()[:5000]
        with zipfile.ZipFile(apk_path) as zf:
            files = zf.namelist()
            return json.dumps({"total_files": len(files), "has_dex": any(f.endswith(".dex") for f in files),
                "has_native_libs": any("lib/" in f for f in files),
                "manifest": "AndroidManifest.xml" in files,
                "signed": any(f.startswith("META-INF/") and f.endswith((".RSA", ".DSA")) for f in files)}, indent=2)

    @server.tool()
    async def sassy_firewall_status() -> str:
        """Check Windows Firewall status."""
        proc = await asyncio.create_subprocess_exec(
            "netsh", "advfirewall", "show", "allprofiles",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        return stdout.decode("utf-8", errors="replace").strip()

    @server.tool()
    async def sassy_open_ports() -> str:
        """List all listening ports."""
        proc = await asyncio.create_subprocess_exec(
            "netstat", "-an",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        lines = [l for l in stdout.decode("utf-8", errors="replace").splitlines() if "LISTENING" in l]
        return "\n".join(lines)

    @server.tool()
    async def sassy_defender_status() -> str:
        """Check Windows Defender status via Event Log (avoids BitDefender ATD flags).
        Falls back to Get-MpComputerStatus if event log query fails."""
        # Primary: Event Log approach (ATD-safe)
        ps_script = (
            "try { "
            "$events = Get-WinEvent -LogName 'Microsoft-Windows-Windows Defender/Operational' -MaxEvents 5 -ErrorAction Stop; "
            "$events | Select TimeCreated,Id,Message | FL "
            "} catch { "
            "try { Get-MpComputerStatus | Select AntivirusEnabled,RealTimeProtectionEnabled,AntivirusSignatureLastUpdated | FL } "
            "catch { 'Defender status unavailable: ' + $_.Exception.Message } "
            "}"
        )
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-Command", ps_script,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        return stdout.decode("utf-8", errors="replace").strip()
