# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-7W5M3Q7NFJ2Y
"""NetworkAudit - Network scanning and monitoring (cross-platform)."""

import asyncio
import re
import shutil

from sassymcp import _platform

_SAFE_HOST = re.compile(r'^[A-Za-z0-9\.\-\:]+$')
_SAFE_PORTS = re.compile(r'^[0-9,\-]+$')
_SAFE_PROFILE = re.compile(r'^[A-Za-z0-9 _\-\.]+$')

# macOS Wi-Fi scan: the legacy `airport -s` was removed in recent macOS, so
# try it first and fall back to system_profiler (lists visible + known nets).
_MAC_WIFI_SCAN = (
    'A=/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport; '
    'if [ -x "$A" ] && "$A" -s 2>/dev/null | grep -q .; then "$A" -s; '
    'else system_profiler SPAirPortDataType; fi'
)


def _register_hooks():
    from sassymcp.modules._hooks import register_hook
    register_hook(
        name="network_recon",
        module="network_audit",
        description="Network reconnaissance and posture assessment — port scans, listening services, ARP, DNS, traceroute.",
        triggers=[
            "scan the network", "scan my network", "port scan", "nmap",
            "what's listening", "open ports", "listening on", "netstat",
            "arp", "wifi scan", "wireless", "trace route", "traceroute",
            "dns lookup", "network audit", "network posture", "what's on",
        ],
        instructions="""
## Network Recon Playbook

### Local-host posture (default starting point)
1. `sassy_netstat` — what's bound to which port. Filter the output by
   listening sockets, note any 0.0.0.0 binds (LAN-exposed) vs 127.0.0.1.
2. `sassy_arp` — who's on the local segment right now (devices the host
   has talked to recently).
3. `sassy_wifi_scan` — visible SSIDs + signal strength when on Wi-Fi.

### Remote target scan
1. `sassy_port_scan target="<host>" ports="<range>"` — wraps nmap (Pro tier).
   Default port set is the top-100 most common; specify "1-65535" for a full
   scan but expect minutes of runtime.
2. `sassy_dns_lookup name="<host>"` for record types A, AAAA, MX, TXT.
3. `sassy_traceroute target="<host>"` for path discovery.

### Discipline
- Always ask the user before scanning anything outside their own network.
  Even non-aggressive scans can trip IDS on third-party hosts.
- `sassy_port_scan` against the LOCAL machine is fine without confirmation
  — that's what `sassy_netstat` essentially does at a higher abstraction.
- For audit trails, all of these write to ~/.sassymcp/audit.log automatically;
  no extra logging needed.

### Combine with
- `sassy_security_audit_certs` for cert-chain inspection on TLS ports you find.
- `sassy_url_security_headers` for HTTP responses on web ports.
""",
    )

try:
    _register_hooks()
except Exception:
    pass


def _validate_host(value: str) -> str:
    """Validate hostname/IP — alphanumeric, dots, hyphens, colons only."""
    if not _SAFE_HOST.match(value):
        raise ValueError(f"Invalid host/target: {value!r}")
    return value


def _validate_ports(value: str) -> str:
    """Validate port spec — digits, commas, hyphens only."""
    if not _SAFE_PORTS.match(value):
        raise ValueError(f"Invalid port spec: {value!r}")
    return value


async def _run_exec(*args, timeout=30):
    """Run a command via subprocess_exec (no shell interpretation)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout.decode("utf-8", errors="replace").strip()
        return out or stderr.decode("utf-8", errors="replace").strip()
    except asyncio.TimeoutError:
        proc.kill()
        return f"Timed out after {timeout}s"
    except FileNotFoundError:
        return f"Error: {args[0]} not found"


def _parse_ports(spec: str) -> list[int]:
    """Expand a validated port spec ('1-1024', '22,80,443', or a mix)."""
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return sorted({p for p in out if 0 < p < 65536})


async def _py_port_scan(target: str, ports_spec: str, connect_timeout: float = 0.4) -> str:
    """Portable async TCP connect scan — the cross-platform fallback when
    nmap is absent (replaces the old Windows-only PowerShell scanner)."""
    ports = _parse_ports(ports_spec)
    open_ports: list[int] = []
    sem = asyncio.Semaphore(200)

    async def _check(port: int) -> None:
        async with sem:
            writer = None
            try:
                fut = asyncio.open_connection(target, port)
                reader, writer = await asyncio.wait_for(fut, timeout=connect_timeout)
                open_ports.append(port)
            except Exception:
                pass
            finally:
                if writer is not None:
                    try:
                        writer.close()
                    except Exception:
                        pass

    await asyncio.gather(*(_check(p) for p in ports))
    if not open_ports:
        return f"No open TCP ports found on {target} (scanned {len(ports)})."
    listed = ", ".join(str(p) for p in sorted(open_ports))
    return f"Open TCP ports on {target} ({len(open_ports)}/{len(ports)}): {listed}"


def register(server):
    @server.tool()
    async def sassy_netstat(filter_str: str = "") -> str:
        """Show active network connections (netstat / ss, host-appropriate)."""
        argv = _platform.pick(
            windows=["netstat", "-ano"],
            macos=["netstat", "-an"],
            linux=(["ss", "-tunap"] if _platform.which("ss") else ["netstat", "-an"]),
        )
        out = await _run_exec(*argv)
        if filter_str:
            lines = [line for line in out.splitlines() if filter_str.lower() in line.lower()]
            return "\n".join(lines[:100])
        return "\n".join(out.splitlines()[:100])

    @server.tool()
    async def sassy_arp_table() -> str:
        """Show ARP table (arp -a; cross-platform)."""
        return await _run_exec("arp", "-a")

    @server.tool()
    async def sassy_wifi_networks() -> str:
        """Scan visible WiFi networks. Windows: netsh wlan. macOS: airport/
        system_profiler. Linux: nmcli/iwlist."""
        argv = _platform.pick(
            windows=["netsh", "wlan", "show", "networks", "mode=bssid"],
            macos=["/bin/sh", "-c", _MAC_WIFI_SCAN],
            linux=(["nmcli", "dev", "wifi", "list"] if _platform.which("nmcli")
                   else ["/bin/sh", "-c", "iwlist scanning 2>&1 || iw dev wlan0 scan 2>&1"]),
        )
        return await _run_exec(*argv, timeout=45)

    @server.tool()
    async def sassy_port_scan(target: str = "127.0.0.1", ports: str = "1-1024") -> str:
        """Port scan. Uses nmap if available, else a portable async TCP scan
        that works on every OS."""
        target = _validate_host(target)
        ports = _validate_ports(ports)
        if shutil.which("nmap"):
            return await _run_exec("nmap", "-p", ports, target, timeout=60)
        return await _py_port_scan(target, ports)

    @server.tool()
    async def sassy_dns_lookup(hostname: str) -> str:
        """DNS lookup (nslookup; cross-platform)."""
        hostname = _validate_host(hostname)
        return await _run_exec("nslookup", hostname)

    @server.tool()
    async def sassy_traceroute(target: str) -> str:
        """Traceroute to target (tracert on Windows, traceroute on POSIX)."""
        target = _validate_host(target)
        argv = _platform.pick(
            windows=["tracert", "-d", target],
            posix=["traceroute", "-n", target],
        )
        return await _run_exec(*argv, timeout=60)

    @server.tool()
    async def sassy_wifi_profile(profile: str = "") -> str:
        """Show WiFi profile details / saved networks. Windows: netsh wlan
        (key=clear reveals the saved password). macOS: Keychain lookup
        (security find-generic-password) / system_profiler. Linux: nmcli."""
        if profile:
            if not _SAFE_PROFILE.match(profile):
                return f"Error: invalid profile name: {profile!r}"
            argv = _platform.pick(
                windows=["netsh", "wlan", "show", "profile", f"name={profile}", "key=clear"],
                macos=["security", "find-generic-password", "-wa", profile],
                linux=["nmcli", "-s", "connection", "show", profile],
            )
            return await _run_exec(*argv)
        argv = _platform.pick(
            windows=["netsh", "wlan", "show", "profiles"],
            macos=["system_profiler", "SPAirPortDataType"],
            linux=(["nmcli", "connection", "show"] if _platform.which("nmcli")
                   else ["/bin/sh", "-c", "ls /etc/NetworkManager/system-connections/ 2>&1"]),
        )
        return await _run_exec(*argv)
