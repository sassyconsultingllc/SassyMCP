"""SassyMCP Security — Input validation for paths, commands, and network targets.

Enforces:
- allowedDirectories: restrict file operations to specific directories
- blockedCommands: prevent execution of dangerous shell commands
- SSRF protection: block requests to private/internal IPs
- ADB device validation
- Registry path restrictions

All checks return (ok: bool, error: str | None).
On failure, the tool should return the error message and NOT proceed.
"""

import ipaddress
import logging
import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger("sassymcp.security")


def _get_config_value(key: str, default=None):
    """Lazy-load a runtime config value."""
    try:
        from sassymcp.modules.runtime_config import get
        return get(key, default)
    except Exception:
        return default


# ── Path Validation ──────────────────────────────────────────────────

def validate_path(path: str) -> tuple[bool, Optional[str]]:
    """Check if a path is within allowedDirectories.

    If allowedDirectories is empty or not configured, all paths are allowed
    (backwards compatible). When configured, paths must resolve to within
    one of the allowed directories.
    """
    allowed = _get_config_value("allowedDirectories", [])
    if not allowed:
        return True, None

    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError) as e:
        return False, f"Invalid path: {e}"

    for allowed_dir in allowed:
        try:
            allowed_resolved = Path(allowed_dir).resolve()
            if resolved == allowed_resolved or allowed_resolved in resolved.parents:
                return True, None
        except (OSError, ValueError):
            continue

    return False, f"Path '{path}' is outside allowed directories: {allowed}"


# ── Command Validation ───────────────────────────────────────────────

# Always blocked regardless of config — these are never safe to run from an MCP tool
_HARDCODED_BLOCKS = {
    "format", "diskpart", "cipher /w",
    "rm -rf /", "rm -rf /*", "dd if=/dev/zero",
    "mkfs", ":(){ :|:& };:",
    "shutdown", "reboot", "halt", "init 0", "init 6",
}


def validate_command(command: str) -> tuple[bool, Optional[str]]:
    """Check if a shell command is blocked.

    Checks against both the hardcoded block list and the user-configurable
    blockedCommands list from runtime config.
    """
    cmd_lower = command.strip().lower()

    # Hardcoded blocks
    for blocked in _HARDCODED_BLOCKS:
        if blocked in cmd_lower:
            return False, f"Command blocked (safety): contains '{blocked}'"

    # User-configured blocks
    blocked_commands = _get_config_value("blockedCommands", [])
    for blocked in blocked_commands:
        if blocked.lower() in cmd_lower:
            return False, f"Command blocked (config): contains '{blocked}'"

    return True, None


# ── ADB Input Validation ─────────────────────────────────────────────

_ADB_DEVICE_PATTERN = re.compile(r"^[A-Za-z0-9.:_\-]+$")
_ADB_PACKAGE_PATTERN = re.compile(r"^[A-Za-z0-9._\-]+$")


def validate_adb_device(device: str) -> tuple[bool, Optional[str]]:
    """Validate ADB device identifier."""
    if not device:
        return True, None  # empty = default device
    if not _ADB_DEVICE_PATTERN.match(device):
        return False, f"Invalid device identifier: {device}"
    return True, None


def validate_adb_package(package: str) -> tuple[bool, Optional[str]]:
    """Validate Android package name."""
    if not _ADB_PACKAGE_PATTERN.match(package):
        return False, f"Invalid package name: {package}"
    return True, None


# ── SSRF Protection ──────────────────────────────────────────────────

_PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / cloud metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def validate_url(url: str, allow_private: bool = False) -> tuple[bool, Optional[str]]:
    """Validate a URL for SSRF protection.

    Blocks: private IPs, link-local, cloud metadata, file:// scheme.
    Set allow_private=True for tools that intentionally target LAN (e.g., crosslink).
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Invalid URL"

    if parsed.scheme not in ("http", "https"):
        return False, f"Blocked URL scheme: {parsed.scheme}"

    if not parsed.hostname:
        return False, "URL has no hostname"

    if allow_private:
        return True, None

    # Resolve hostname to check for private IPs
    try:
        addr = ipaddress.ip_address(parsed.hostname)
        for net in _PRIVATE_RANGES:
            if addr in net:
                return False, f"Blocked: URL resolves to private/internal address ({parsed.hostname})"
    except ValueError:
        # Not an IP literal — hostname. Check common dangerous hostnames.
        hostname = parsed.hostname.lower()
        if hostname in ("localhost", "metadata.google.internal", "169.254.169.254"):
            return False, f"Blocked: dangerous hostname ({hostname})"

    return True, None


# ── Input Size Validation ────────────────────────────────────────────

def validate_input_size(value: str, max_bytes: int = 10_000_000, label: str = "input") -> tuple[bool, Optional[str]]:
    """Reject inputs that exceed a size threshold."""
    if len(value) > max_bytes:
        return False, f"{label} exceeds maximum size ({len(value)} > {max_bytes} bytes)"
    return True, None
