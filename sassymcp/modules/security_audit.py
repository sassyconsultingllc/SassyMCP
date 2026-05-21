"""SecurityAudit - Hash verification, permissions, certs, APK analysis, forensics."""

import asyncio
import hashlib
import json
from pathlib import Path

from sassymcp.modules._security import is_sensitive_read_path


def _register_hooks():
    from sassymcp.modules._hooks import register_hook

    register_hook(
        name="security_scan",
        module="security_audit",
        description="System security assessment — firewall, defender, certs, permissions, autoruns",
        triggers=["security scan", "security audit", "check security", "am i secure", "hardening check",
                  "system security", "security assessment"],
        instructions="""
## System Security Assessment Playbook

Evaluate the system's security posture across all available vectors.

### 1. PERIMETER (network exposure)
- sassy_open_ports — what's listening? Flag unexpected services.
- sassy_firewall_status — all profiles ON? Policy correct?
- sassy_netstat — active connections to unknown IPs?

### 2. DEFENSE (endpoint protection)
- sassy_defender_status — Defender active? Signatures current?
- sassy_autorun_entries — anything suspicious in startup?
- sassy_eventlog_search keyword="error" or keyword="warning" — recent security events

### 3. CERTIFICATES (TLS health)
- sassy_cert_check on any exposed services — valid? Expiring soon?
- Check cert chain, issuer, SAN coverage

### 4. FILE INTEGRITY (spot checks)
- sassy_hash_file on critical binaries/configs — compare against known-good
- sassy_file_permissions on sensitive directories — ACLs correct?

### 5. PROCESS AUDIT
- sassy_processes — anything unexpected running? High CPU/memory anomalies?
- Cross-reference with sassy_autorun_entries — is everything accounted for?

### Report:
- CRITICAL: immediate action required (exposed services, disabled firewall, malware indicators)
- WARNING: should fix soon (expiring certs, missing hardening, weak permissions)
- INFO: noted for awareness (configuration details, version info)
""",
    )

    register_hook(
        name="forensics",
        module="security_audit",
        description="Digital forensics investigation — evidence collection, timeline analysis",
        triggers=["forensics", "investigate", "breach", "compromise", "incident", "suspicious activity",
                  "was i hacked", "malware check"],
        instructions="""
## Digital Forensics Playbook

PRESERVE EVIDENCE FIRST. Read-only operations until you understand the scope.

### Phase 1: TRIAGE (read-only)
- sassy_processes — snapshot running processes NOW
- sassy_netstat — snapshot active connections NOW
- sassy_open_ports — what's listening that shouldn't be?
- sassy_autorun_entries — new/unknown startup items?
- sassy_eventlog count=50 — recent system events
- sassy_defender_status — any recent detections?

### Phase 2: TIMELINE
- sassy_eventlog_search keyword="<suspicious term>" — correlate events
- sassy_audit_log — SassyMCP's own activity log
- sassy_file_info on suspicious files — timestamps (created, modified, accessed)

### Phase 3: INDICATORS
- sassy_hash_file on suspicious files — check against known malware hashes
- sassy_dns_lookup on suspicious domains from netstat
- sassy_cert_check on suspicious TLS connections

### Rules:
- NEVER modify files or kill processes until evidence is documented
- Hash before touching — sassy_hash_file first
- If active threat found: recommend isolation, don't remediate without approval
""",
    )

try:
    _register_hooks()
except Exception:
    pass


def register(server):
    @server.tool()
    async def sassy_hash_file(path: str, algorithm: str = "sha256") -> str:
        """Compute file hash. algorithm: md5, sha1, sha256, sha512.

        Refuses paths in the sensitive-read denylist (SSH keys, credential
        stores, browser login DBs, Windows SAM/SECURITY hives, /etc/shadow,
        SassyMCP's own tokens.json, etc.) so an attacker-controlled LLM
        can't use the hash function for confirmation-of-existence or
        partial-content exfiltration on credential files.
        """
        algos = {"md5": hashlib.md5, "sha1": hashlib.sha1, "sha256": hashlib.sha256, "sha512": hashlib.sha512}
        if algorithm not in algos: return f"Error: use {', '.join(algos)}"
        denied, reason = is_sensitive_read_path(path)
        if denied:
            return json.dumps({"error": "Refused: sensitive read path", "reason": reason})
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
        """Analyze APK: permissions, signatures, package info.

        Validates the target before opening: refuses sensitive-read paths
        and requires a .apk extension so the tool can't be repurposed as
        a "dump arbitrary file as zip" primitive against, say, an MSI or
        an Outlook PST.
        """
        import shutil
        import zipfile
        denied, reason = is_sensitive_read_path(apk_path)
        if denied:
            return json.dumps({"error": "Refused: sensitive read path", "reason": reason})
        if not str(apk_path).lower().endswith(".apk"):
            return json.dumps({
                "error": "sassy_apk_info refuses non-.apk inputs",
                "hint": "Pass a path ending in .apk. For arbitrary zip inspection use sassy_unzip.",
            })
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
