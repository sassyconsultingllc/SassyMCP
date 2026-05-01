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
import socket
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

    Matches against the hardcoded block list and the user-configurable
    blockedCommands list, scanning a quoted-string-stripped view of the
    command so words inside string literals do not trip the block. The
    `interceptor.scanStringLiterals` config key (default false) opts back
    in to the strict raw-text scan when callers need it.
    """
    ok, _tier, err = validate_command_tiered(command)
    return ok, err


def validate_command_tiered(command: str) -> tuple[bool, str, Optional[str]]:
    """Block-list scan that distinguishes real matches from string-literal hits.

    Returns (ok, tier, error_message).
      - ok=True, tier="" — command passes
      - ok=False, tier="high" — match against the live (string-stripped) form;
        the keyword is actually being executed.
      - ok=False, tier="low" — match only appears inside a quoted string
        literal; a literal `format` inside `"format the disk"` is data, not
        an execution. Callers (e.g. sassy_shell) may downgrade to log+allow.

    The strict raw-text scan can be re-enabled via the
    `interceptor.scanStringLiterals` config key — when true, a "low" hit
    is reported as "high" so existing strict callers see no behavior change.
    """
    cmd_raw = command.strip().lower()
    cmd_stripped = _strip_quoted_strings(cmd_raw)

    scan_literals = bool(_get_config_value("interceptor.scanStringLiterals", False))

    def _classify(needle: str, label: str) -> tuple[bool, str, Optional[str]]:
        if needle in cmd_stripped:
            return False, "high", f"Command blocked ({label}): contains '{needle}'"
        if needle in cmd_raw:
            tier = "high" if scan_literals else "low"
            suffix = "" if scan_literals else " inside a string literal"
            return False, tier, f"Command contains '{needle}'{suffix}"
        return True, "", None

    for blocked in _HARDCODED_BLOCKS:
        ok, tier, err = _classify(blocked, "safety")
        if not ok:
            return ok, tier, err

    blocked_commands = _get_config_value("blockedCommands", [])
    for blocked in blocked_commands:
        ok, tier, err = _classify(blocked.lower(), "config")
        if not ok:
            return ok, tier, err

    return True, "", None


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


_URL_MAX_LEN = 2048


def _is_private_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(addr in net for net in _PRIVATE_RANGES)


def validate_url(url: str, allow_private: bool = False) -> tuple[bool, Optional[str]]:
    """Validate a URL for SSRF protection.

    Blocks: private IPs (literal or via DNS resolution), link-local, cloud
    metadata, non-http(s) schemes. Set allow_private=True for tools that
    intentionally target LAN (e.g., crosslink).

    DNS resolution: hostnames are resolved via getaddrinfo and EVERY returned
    address is checked. This closes the trivial SSRF where evil.example.com
    points an A-record at 10.0.0.1. Note this does NOT defeat live DNS-rebinding
    attacks where the resolver returns a public IP at validation time and a
    private IP when the request is actually issued — defeating that requires
    pinning the resolved address through to the HTTP client. Callers that need
    that guarantee must resolve once here and then connect to the resolved IP.
    """
    if not isinstance(url, str) or len(url) > _URL_MAX_LEN:
        return False, f"URL exceeds max length ({_URL_MAX_LEN})"

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

    hostname = parsed.hostname.lower()

    # Always block dangerous hostnames by name, even before DNS — defends
    # against /etc/hosts overrides and resolver weirdness.
    if hostname in ("localhost", "metadata.google.internal", "metadata.azure.com"):
        return False, f"Blocked: dangerous hostname ({hostname})"

    # Literal IP — check directly.
    try:
        addr = ipaddress.ip_address(hostname)
        if _is_private_ip(addr):
            return False, f"Blocked: URL resolves to private/internal address ({hostname})"
        return True, None
    except ValueError:
        pass

    # Hostname — resolve and check every returned address.
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        return False, f"Blocked: hostname resolution failed ({hostname}: {e})"

    for family, _type, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _is_private_ip(addr):
            return False, (
                f"Blocked: hostname '{hostname}' resolves to "
                f"private/internal address ({ip_str})"
            )

    return True, None


# ── Delete Command Detection ────────────────────────────────────────

# Commands that indicate file/directory deletion intent.
# These are intercepted and targets are moved to a _DELETE_ staging folder
# instead of being destroyed — minimising data loss from AI hallucinations.
_DELETE_KEYWORDS = frozenset({
    "rm", "rmdir", "unlink",                  # Unix / WSL
    "del", "erase", "rd",                     # Windows CMD
    "remove-item", "ri", "rni",               # PowerShell (incl. aliases)
    "sdelete", "sdelete64",                   # Sysinternals secure-delete
})

# Shell wrappers whose payload must be recursively scanned.
# Format: command-name -> flags that consume the next arg as a nested command.
_WRAPPER_CMDS = {
    "powershell":    {"-c", "-command", "-encodedcommand", "-enc", "-e"},
    "powershell.exe":{"-c", "-command", "-encodedcommand", "-enc", "-e"},
    "pwsh":          {"-c", "-command", "-encodedcommand", "-enc", "-e"},
    "pwsh.exe":      {"-c", "-command", "-encodedcommand", "-enc", "-e"},
    "cmd":           {"/c", "/k", "/r"},
    "cmd.exe":       {"/c", "/k", "/r"},
    "wsl":           {"-e", "--exec", "--"},
    "wsl.exe":       {"-e", "--exec", "--"},
    "bash":          {"-c"},
    "sh":            {"-c"},
    "zsh":           {"-c"},
}

# Flags that carry a base64-encoded PowerShell command payload.
_ENCODED_FLAGS = {"-encodedcommand", "-enc", "-e"}

# Destructive patterns that aren't bare keywords.
# Evaluated against the lowered command segment after $var= prefix stripping.
_DESTRUCTIVE_PATTERNS = [
    (re.compile(r"\bclear-content\b"),                                                                    "clear-content"),
    # Set-Content with a LITERAL empty string as its value is a wipe.
    # Require an actual empty quoted string (not just "-value <anything>").
    (re.compile(r"\bset-content\b[^|;&\n]*?-value\s+(?:''|\"\")(?:\s|$)"),                                "set-content empty"),
    (re.compile(r"\[system\.io\.file\]::delete"),                                                         ".net file.delete"),
    (re.compile(r"\[system\.io\.directory\]::delete"),                                                    ".net directory.delete"),
    (re.compile(r"\[io\.file\]::delete"),                                                                 ".net file.delete"),
    # Out-File -Force / -Overwrite replaces any existing file.
    (re.compile(r"\bout-file\b[^|;&\n]*\s-force\b"),                                                      "out-file -force"),
    (re.compile(r"\bout-file\b[^|;&\n]*\s-overwrite\b"),                                                  "out-file -overwrite"),
    # New-Item -Force on an existing FILE replaces it. Directory/symlink/junction
    # creation is idempotent with -Force (no-op if target exists) and safe.
    (re.compile(r"\bnew-item\b(?![^|;&\n]*-itemtype\s+(?:directory|symboliclink|junction))[^|;&\n]*\s-force\b"), "new-item -force"),
    # CMD copy/xcopy /y silently overwrite destination.
    (re.compile(r"(?:^|[;&|])\s*copy\b[^|;&\n]*\s/y\b"),                                                  "copy /y"),
    (re.compile(r"\bxcopy\b[^|;&\n]*\s/y\b"),                                                             "xcopy /y"),
    # robocopy /MIR and /PURGE delete files in destination.
    (re.compile(r"\brobocopy\b[^|;&\n]*\s/mir\b"),                                                        "robocopy /mir"),
    (re.compile(r"\brobocopy\b[^|;&\n]*\s/purge\b"),                                                      "robocopy /purge"),
    # Truncate-by-redirect: a single `>` that is NOT part of `>>` (append),
    # `&>`/`2>` (stream redirect), etc., pointing at a filename.
    # Exemption: redirects to scratch/temp locations are benign stdout capture.
    # Matches `> "%TEMP%\...`, `> $env:TEMP\...`, `> /tmp/...`, `> %TMP%\...`.
    (re.compile(
        r"(?<![>&0-9])>(?!>)\s*"
        r"(?!\"?%TEMP%|\"?%TMP%|\"?\$env:TEMP|\"?\$env:TMP|\"?/tmp/|\"?/var/tmp/)"
        r"[^\s&|;<>]"
    ), "truncate-by-redirect"),
    (re.compile(r"\bmove-item\b[^|;&\n]*\s+\$null\b"),                                                    "move-item to $null"),
    (re.compile(r"\bout-null\b[^|;&\n]*>\s*\$null"),                                                      "redirect to $null"),
]

# Risk tier per destructive pattern label. Used by sassy_shell to decide:
#   low    -> log and run (no prompt)
#   medium -> confirm-token round-trip (interceptor.destructiveAction=confirm)
#             or block (default)
#   high   -> confirm-token + typed-phrase requirement, or block
# Labels not listed here default to "medium".
_PATTERN_TIERS: dict[str, str] = {
    "clear-content":         "medium",
    "set-content empty":     "medium",
    ".net file.delete":      "medium",
    ".net directory.delete": "high",
    "out-file -force":       "medium",
    "out-file -overwrite":   "medium",
    "new-item -force":       "low",
    "copy /y":               "low",
    "xcopy /y":              "low",
    "robocopy /mir":         "high",
    "robocopy /purge":       "high",
    "truncate-by-redirect":  "low",
    "move-item to $null":    "medium",
    "redirect to $null":     "low",
}


def pattern_tier(label: str) -> str:
    """Return the risk tier ('low'|'medium'|'high') for a pattern label."""
    base = label.split(":", 1)[-1]  # strips wrapper prefix like 'encodedcommand:'
    return _PATTERN_TIERS.get(base, "medium")


# Assignment prefixes in PowerShell that would otherwise make the wrapped
# delete keyword invisible to first-word matching.
_PS_ASSIGNMENT_PREFIX = re.compile(r"^\$\w+\s*=\s*")

# Match the contents of single/double/backtick-quoted runs that don't span
# newlines. Used to neutralize destructive characters like `>` that live
# inside string literals — those can never trigger a real shell redirect.
_QUOTED_RUN = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"|`[^`\n]*`")


def _strip_quoted_strings(s: str) -> str:
    """Replace quoted-string contents with neutral filler, preserving length.

    Rationale: a literal `>` inside `"V:\\logs\\bridge.out.log"` cannot be a
    shell redirect — it's data inside a parameter value. Running the
    `truncate-by-redirect` regex against the raw command flags it as
    destructive anyway. Pre-stripping the contents (but keeping the quote
    characters and overall length so offsets stay sensible) lets pattern
    matching see only the shell metacharacters that are actually live.

    Quote characters themselves are kept so first-word/keyword matching is
    still well-formed; only the contents are filled with `x` (a character
    that won't match any destructive pattern).
    """
    def replacer(m: re.Match) -> str:
        run = m.group(0)
        return run[0] + ("x" * (len(run) - 2)) + run[-1]
    return _QUOTED_RUN.sub(replacer, s)


def _decode_powershell_base64(payload: str) -> Optional[str]:
    """Best-effort decode of a PowerShell -EncodedCommand argument.

    PowerShell encodes with UTF-16-LE then base64. Returns decoded text
    or None if the payload isn't decodable.
    """
    try:
        import base64
        cleaned = payload.strip().strip("'\"")
        # Pad to multiple of 4 for base64.
        cleaned += "=" * (-len(cleaned) % 4)
        raw = base64.b64decode(cleaned, validate=False)
        return raw.decode("utf-16-le", errors="strict")
    except Exception:
        return None


def _scan_segment(seg_lower: str, seg_orig: str) -> tuple[bool, str]:
    """Scan a single command segment (already split on ; & | newlines).

    Takes BOTH the lowered segment (for keyword/pattern matching) and the
    original-case segment (for base64 payloads that must not be lowercased).
    """
    stripped_lower = seg_lower.strip()
    stripped_orig = seg_orig.strip()
    if not stripped_lower:
        return False, ""

    # Strip PowerShell assignment prefix ("$null = ri foo" -> "ri foo").
    stripped_lower = _PS_ASSIGNMENT_PREFIX.sub("", stripped_lower)
    stripped_orig = _PS_ASSIGNMENT_PREFIX.sub("", stripped_orig)

    # Destructive regex patterns — run first so they catch things keywords miss.
    # Run patterns against a quoted-string-stripped copy so a literal `>`
    # inside a parameter value (e.g. "-RedirectStandardOutput \"V:\\logs\\f.log\"")
    # cannot impersonate a real shell redirect.
    pattern_subject = _strip_quoted_strings(stripped_lower)
    for pat, label in _DESTRUCTIVE_PATTERNS:
        if pat.search(pattern_subject):
            return True, label

    words_lower = stripped_lower.split()
    words_orig = stripped_orig.split()
    if not words_lower:
        return False, ""

    first = words_lower[0].lstrip("&").lstrip(".")
    first = first.strip("'\"")

    # Direct keyword match.
    if first in _DELETE_KEYWORDS:
        return True, first

    # Shell wrapper — recursively scan the inner payload.
    if first in _WRAPPER_CMDS:
        flags = _WRAPPER_CMDS[first]
        i = 1
        while i < len(words_lower):
            tok = words_lower[i]
            if tok in flags and i + 1 < len(words_lower):
                # Base64-encoded PowerShell payload — decode the ORIGINAL-case
                # token (base64 is case-sensitive).
                if tok in _ENCODED_FLAGS:
                    payload = words_orig[i + 1] if i + 1 < len(words_orig) else words_lower[i + 1]
                    decoded = _decode_powershell_base64(payload)
                    if decoded:
                        is_del, kw = detect_delete_intent(decoded)
                        if is_del:
                            return True, f"encodedcommand:{kw}"
                    i += 2
                    continue
                inner = " ".join(words_lower[i + 1:])
                if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in ("'", '"'):
                    inner = inner[1:-1]
                return detect_delete_intent(inner)
            if tok.startswith("-") or tok.startswith("/"):
                i += 1
                continue
            # First positional token after a shell name — treat as command.
            inner = " ".join(words_lower[i:])
            return detect_delete_intent(inner)
        return False, ""

    return False, ""


def detect_delete_intent(command: str) -> tuple[bool, str]:
    """Detect if a command attempts to delete files/directories.

    Returns (is_delete, matched_keyword).
    Delete commands are intercepted — targets are moved to a _DELETE_
    staging folder instead of being destroyed.

    Handles: direct keywords, PowerShell aliases (ri/rni), shell wrappers
    (powershell/cmd/wsl/bash -c), .NET File.Delete, Clear-Content,
    truncate-by-redirect, and segmented commands joined by ; & | \\n.
    """
    # Split on the original-case command so we can pass both forms to
    # _scan_segment (base64 payloads must not be lowercased).
    segments_orig = re.split(r'[;&|\n]+', command)
    for seg_orig in segments_orig:
        is_del, kw = _scan_segment(seg_orig.lower(), seg_orig)
        if is_del:
            return True, kw
    return False, ""


# ── Protected Paths — never delete/overwrite ────────────────────────

def _protected_roots() -> list[Path]:
    """Paths that no tool should delete, move, or overwrite."""
    roots = []
    try:
        # The SassyMCP source tree itself.
        roots.append(Path(__file__).resolve().parent.parent)  # sassymcp/
    except Exception:
        pass
    # User config/audit (honors $SASSYMCP_HOME for dual-instance setups).
    try:
        from sassymcp._paths import HOME as _sassy_home
        roots.append(_sassy_home)
    except Exception:
        roots.append(Path.home() / ".sassymcp")
    return roots


def is_protected_path(path: str | Path) -> tuple[bool, Optional[str]]:
    """Check if a path is protected from deletion/overwrite.

    Uses resolve() for the check so that:
      - .. traversal is collapsed ("V:/Projects/SassyMCP/_DELETE_/../sassymcp/.." → caught)
      - Windows 8.3 short names are expanded ("V:/PROJEC~1/.." → caught)
      - Symlinks are followed so that a symlink pointing INTO a protected
        tree is correctly refused

    Note: the MOVE logic in shell.py / fileops.py still uses absolute()
    (not resolve()) so that symlinks are MOVED as symlinks. This check is
    about "what does this target on the real filesystem" — the move is
    about "what literal entry is this."
    """
    try:
        p_abs = Path(path).absolute()
    except (OSError, ValueError):
        return False, None

    # Try to resolve (collapse .., expand 8.3 names, follow symlinks).
    # strict=False returns the best-effort resolved path even if the
    # terminal component does not exist.
    try:
        p = p_abs.resolve(strict=False)
    except (OSError, ValueError):
        p = p_abs

    # The staging folder itself — never recurse into it.
    if p.name == "_DELETE_":
        return True, "path is a _DELETE_ staging folder"

    for root in _protected_roots():
        try:
            root_resolved = root.resolve(strict=False)
        except (OSError, ValueError):
            try:
                root_resolved = root.absolute()
            except (OSError, ValueError):
                continue
        if p == root_resolved or root_resolved in p.parents:
            # Exemption for paths inside a staging folder THAT LIVES INSIDE
            # the protected root. Example: sassymcp/modules/_DELETE_/old.py
            # is ok to touch — it's already staged.
            # We check this on the RESOLVED path, so "_DELETE_/.." traversal
            # no longer bypasses protection (parts after resolve() won't
            # contain _DELETE_ if it was escaped).
            if "_DELETE_" in p.parts:
                return False, None
            return True, f"path is inside protected root: {root_resolved}"

    return False, None


# ── Input Size Validation ────────────────────────────────────────────

def validate_input_size(value: str, max_bytes: int = 10_000_000, label: str = "input") -> tuple[bool, Optional[str]]:
    """Reject inputs that exceed a size threshold."""
    if len(value) > max_bytes:
        return False, f"{label} exceeds maximum size ({len(value)} > {max_bytes} bytes)"
    return True, None
