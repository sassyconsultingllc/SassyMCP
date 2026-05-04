"""NetworkAudit - Network scanning and monitoring."""

import asyncio
import re
import shutil

_SAFE_HOST = re.compile(r'^[A-Za-z0-9\.\-\:]+$')
_SAFE_PORTS = re.compile(r'^[0-9,\-]+$')
_SAFE_PROFILE = re.compile(r'^[A-Za-z0-9 _\-\.]+$')


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
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode("utf-8", errors="replace").strip()
    except asyncio.TimeoutError:
        proc.kill()
        return f"Timed out after {timeout}s"
    except FileNotFoundError:
        return f"Error: {args[0]} not found"


def register(server):
    @server.tool()
    async def sassy_netstat(filter_str: str = "") -> str:
        """Show active network connections."""
        out = await _run_exec("netstat", "-ano")
        if filter_str:
            lines = [line for line in out.splitlines() if filter_str.lower() in line.lower()]
            return "\n".join(lines[:100])
        return "\n".join(out.splitlines()[:100])

    @server.tool()
    async def sassy_arp_table() -> str:
        """Show ARP table."""
        return await _run_exec("arp", "-a")

    @server.tool()
    async def sassy_wifi_networks() -> str:
        """Scan visible WiFi networks."""
        return await _run_exec("netsh", "wlan", "show", "networks", "mode=bssid")

    @server.tool()
    async def sassy_port_scan(target: str = "127.0.0.1", ports: str = "1-1024") -> str:
        """Port scan. Uses nmap if available."""
        target = _validate_host(target)
        ports = _validate_ports(ports)
        if shutil.which("nmap"):
            return await _run_exec("nmap", "-p", ports, target, timeout=60)
        ps_script = f"1..1024 | % {{ $t=New-Object Net.Sockets.TcpClient; try {{ $t.ConnectAsync('{target}',$_).Wait(200)|Out-Null; if($t.Connected){{$_}} }} catch {{}} finally {{ $t.Dispose() }} }}"
        return await _run_exec("powershell.exe", "-NoProfile", "-Command", ps_script, timeout=60)

    @server.tool()
    async def sassy_dns_lookup(hostname: str) -> str:
        """DNS lookup."""
        hostname = _validate_host(hostname)
        return await _run_exec("nslookup", hostname)

    @server.tool()
    async def sassy_traceroute(target: str) -> str:
        """Traceroute to target."""
        target = _validate_host(target)
        return await _run_exec("tracert", "-d", target, timeout=60)

    @server.tool()
    async def sassy_wifi_profile(profile: str = "") -> str:
        """Show WiFi profile details."""
        if profile:
            if not _SAFE_PROFILE.match(profile):
                return f"Error: invalid profile name: {profile!r}"
            return await _run_exec("netsh", "wlan", "show", "profile", f"name={profile}", "key=clear")
        return await _run_exec("netsh", "wlan", "show", "profiles")
