"""SecurityAudit - Hash verification, permissions, certs, APK analysis, forensics."""

import asyncio
import hashlib
import json
from pathlib import Path

from sassymcp import _platform
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
        """Check file/directory permissions. Windows: ACLs (Get-Acl). macOS:
        POSIX mode + ACLs + flags (ls -led@). Linux: getfacl, or ls -lad."""
        # argv form means `path` is a single token (no shell), so no escaping
        # is needed on POSIX; the PS branch still escapes for its quoted string.
        safe_path = path.replace("'", "''")
        argv = _platform.pick(
            windows=["powershell.exe", "-NoProfile", "-Command",
                     f"Get-Acl '{safe_path}' | Format-List"],
            macos=["ls", "-l", "-e", "-d", "-@", path],
            linux=(["getfacl", path] if _platform.which("getfacl")
                   else ["ls", "-l", "-a", "-d", path]),
            feature="file permissions",
        )
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        out = stdout.decode("utf-8", errors="replace").strip()
        return out or stderr.decode("utf-8", errors="replace").strip()

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
        """Check the host firewall. Windows: netsh advfirewall. macOS:
        Application Firewall (socketfilterfw). Linux: ufw, or iptables."""
        argv = _platform.pick(
            windows=["netsh", "advfirewall", "show", "allprofiles"],
            macos=["/usr/libexec/ApplicationFirewall/socketfilterfw",
                   "--getglobalstate", "--getstealthmode",
                   "--getblockall", "--getloggingmode"],
            linux=(["ufw", "status", "verbose"] if _platform.which("ufw")
                   else ["iptables", "-L", "-n"]),
            feature="firewall status",
        )
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        out = stdout.decode("utf-8", errors="replace").strip()
        return out or stderr.decode("utf-8", errors="replace").strip()

    @server.tool()
    async def sassy_open_ports() -> str:
        """List all listening ports (netstat on every OS; LISTEN/LISTENING)."""
        proc = await asyncio.create_subprocess_exec(
            "netstat", "-an",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        # Windows prints state "LISTENING"; macOS/Linux print "LISTEN".
        lines = [l for l in stdout.decode("utf-8", errors="replace").splitlines()
                 if "LISTEN" in l.upper()]
        return "\n".join(lines)

    @server.tool()
    async def sassy_defender_status() -> str:
        """Endpoint-protection status. Windows: Defender via Event Log (ATD-safe),
        falling back to Get-MpComputerStatus. macOS has no Defender — reports the
        equivalent posture: Gatekeeper (spctl), SIP (csrutil), XProtect. Linux:
        reports ClamAV if installed."""
        # Windows: Event Log approach (ATD-safe), falls back to Get-MpComputerStatus.
        win_ps = (
            "try { "
            "$events = Get-WinEvent -LogName 'Microsoft-Windows-Windows Defender/Operational' -MaxEvents 5 -ErrorAction Stop; "
            "$events | Select TimeCreated,Id,Message | FL "
            "} catch { "
            "try { Get-MpComputerStatus | Select AntivirusEnabled,RealTimeProtectionEnabled,AntivirusSignatureLastUpdated | FL } "
            "catch { 'Defender status unavailable: ' + $_.Exception.Message } "
            "}"
        )
        mac_sh = (
            'echo "== Gatekeeper =="; spctl --status 2>&1; '
            'echo "== System Integrity Protection =="; csrutil status 2>&1; '
            'echo "== XProtect =="; '
            'system_profiler SPInstallHistoryDataType 2>/dev/null '
            '| grep -A2 -i "xprotect\\|MRT\\|Gatekeeper" | head -30; '
            'echo "(macOS uses Gatekeeper + XProtect + MRT instead of Defender)"'
        )
        linux_sh = (
            'if command -v clamscan >/dev/null 2>&1; then clamscan --version; '
            'systemctl is-active clamav-daemon 2>/dev/null; '
            'else echo "No on-access AV (ClamAV) detected. Check distro security tooling."; fi'
        )
        argv = _platform.pick(
            windows=["powershell.exe", "-NoProfile", "-Command", win_ps],
            macos=["/bin/sh", "-c", mac_sh],
            linux=["/bin/sh", "-c", linux_sh],
            feature="endpoint protection status",
        )
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        out = stdout.decode("utf-8", errors="replace").strip()
        return out or stderr.decode("utf-8", errors="replace").strip()
