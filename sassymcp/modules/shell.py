"""Shell - Execute PowerShell, CMD, and WSL commands.

Includes automatic syntax normalization:
- Converts && chains to PowerShell-compatible ; separators
- Converts cd/pushd to Set-Location for PowerShell
- Passes CMD and WSL commands through unchanged

Security:
- Enforces blockedCommands from runtime config
- Enforces hardcoded block list for destructive operations
- Intercepts delete commands and moves targets to _DELETE_ staging folder

Timeout handling:
- Hard cap at _MAX_TIMEOUT (300s) for in-process subprocess wait.
- Calls requesting more than _MCP_SAFE_TIMEOUT (120s) auto-promote to a
  background session via session.start_session_impl, because synchronous
  subprocess.communicate() past the MCP client's ~240s response wall
  wedges the connection and silently drops the response.
"""

import asyncio
import glob as glob_mod
import json
import os
import re
import shlex
import shutil
import sys
import uuid
from pathlib import Path

from sassymcp.modules._security import (
    detect_delete_intent,
    is_protected_path,
    pattern_tier,
    validate_command_tiered,
)
from sassymcp.modules import _confirm
from sassymcp.modules import audit as _audit


_MAX_TIMEOUT = 300
# Below the MCP client-side response wall (~240s). Any sassy_shell call
# requesting more than this gets auto-promoted to a background session,
# because subprocess.communicate() blocking past the wall causes the JSON-RPC
# response to land on a dead connection and wedges all subsequent tool calls.
_MCP_SAFE_TIMEOUT = 120
_STAGING_FOLDER = "_DELETE_"


def _cfg(key: str, default):
    """Lazy runtime-config lookup. Avoids circular imports at module load."""
    try:
        from sassymcp.modules.runtime_config import get
        return get(key, default)
    except Exception:
        return default


_SHELL_MAP = {
    "powershell": ["powershell.exe", "-NoProfile", "-Command"],
    "cmd":        ["cmd.exe", "/c"],
    "wsl":        ["wsl", "--", "bash", "-c"],
}

# CMD flag allowlist — only these short /X tokens are treated as flags.
# Everything else starting with "/" is a POSIX-style path target.
_CMD_FLAG_ALLOWLIST = {
    "/s", "/q", "/f", "/p", "/a", "/ah", "/ar", "/as", "/aa", "/q",
    "/r", "/e", "/d", "/b", "/v", "/l", "/y", "/-y",
}

# PowerShell flags whose next token is NOT a deletion target
_PS_SKIP_FLAGS = {"-include", "-exclude", "-filter", "-depth", "-recurse", "-force"}
# PowerShell flags whose next token IS the target path
_PS_PATH_FLAGS = {"-path", "-literalpath"}


def _split_preserve_paths(command: str) -> list[str]:
    """Split a command preserving Windows paths.

    shlex with posix=True mangles backslashes ('C:\\foo' -> 'C:foo').
    On Windows we use posix=False; elsewhere posix=True.
    Falls back to str.split() if shlex raises.
    """
    try:
        use_posix = (os.name != "nt")
        return shlex.split(command, posix=use_posix)
    except ValueError:
        return command.split()


def _parse_delete_targets(command: str) -> list[str]:
    """Extract target file/directory paths from a delete command."""
    parts = _split_preserve_paths(command)
    targets: list[str] = []
    skip_next = False

    for i, part in enumerate(parts):
        if skip_next:
            skip_next = False
            continue
        if i == 0:                           # skip the command keyword itself
            continue
        # Strip quotes that posix=False leaves behind.
        clean = part.strip("'\"")
        lower = clean.lower()
        if lower in _PS_SKIP_FLAGS:          # flag that consumes the next token
            skip_next = True
            continue
        if lower in _PS_PATH_FLAGS:          # flag whose value IS a target
            if i + 1 < len(parts):
                targets.append(parts[i + 1].strip("'\""))
            skip_next = True
            continue
        if clean.startswith("-"):            # other PS/Unix flags
            continue
        # CMD flag? Must be in the allowlist, otherwise treat as a path.
        if clean.startswith("/") and lower in _CMD_FLAG_ALLOWLIST:
            continue
        if clean:
            targets.append(clean)

    # Expand globs; preserve the original literal if the glob matches nothing
    # (so the caller can report "not found" rather than silently losing it).
    expanded: list[str] = []
    for t in targets:
        if any(c in t for c in ("*", "?", "[")):
            matches = glob_mod.glob(t)
            expanded.extend(matches if matches else [t])
        else:
            expanded.append(t)
    return expanded


async def _safe_move_to_staging(targets: list[str], keyword: str, raw_command: str) -> str:
    """Move deletion targets to a _DELETE_ staging folder in the same directory."""
    if not targets:
        msg = (
            f"Delete command blocked ('{keyword}'). "
            "Could not parse target paths from command.\n"
            "Use sassy_safe_delete(path) to move items to the _DELETE_ staging folder."
        )
        _audit.log_intercept("sassy_shell", keyword, raw_command, [], ["no targets parsed"])
        return msg

    results: list[str] = []
    for target in targets:
        # absolute() — NOT resolve() — so symlinks are moved as symlinks
        # instead of dragging their real target into staging.
        try:
            p = Path(target).absolute()
        except (OSError, ValueError) as e:
            results.append(f"  Error resolving path {target!r}: {e}")
            continue

        if not p.exists() and not p.is_symlink():
            results.append(f"  Skipped (not found): {target}")
            continue

        # Never let the interceptor delete/move the SassyMCP source tree
        # or the staging folder itself.
        prot, reason = is_protected_path(p)
        if prot:
            results.append(f"  REFUSED (protected): {p}  [{reason}]")
            continue

        staging = p.parent / _STAGING_FOLDER
        try:
            staging.mkdir(exist_ok=True)
        except OSError as e:
            results.append(f"  Error creating staging folder {staging}: {e}")
            continue

        dest = staging / p.name
        if dest.exists():
            stem = p.stem
            suffix = p.suffix if p.is_file() else ""
            counter = 1
            while dest.exists():
                new_name = f"{stem}_{counter}{suffix}" if suffix else f"{p.name}_{counter}"
                dest = staging / new_name
                counter += 1

        try:
            shutil.move(str(p), str(dest))
            results.append(f"  Moved: {p} -> {dest}")
        except (OSError, shutil.Error) as e:
            results.append(f"  Error moving {target}: {e}")

    header = (
        f"Delete command blocked ('{keyword}'). "
        f"Items moved to {_STAGING_FOLDER}/ staging folder for review:"
    )
    _audit.log_intercept("sassy_shell", keyword, raw_command, targets, results)
    return header + "\n" + "\n".join(results)


def _normalize_for_powershell(command: str) -> str:
    """Convert bash/cmd syntax to PowerShell equivalents."""
    command = command.replace(" && ", "; ")
    parts = command.split(" || ")
    if len(parts) > 1:
        result = parts[0]
        for part in parts[1:]:
            result = f"{result}; if ($LASTEXITCODE -ne 0) {{ {part} }}"
        command = result
    command = re.sub(r'^cd\s+/d\s+(.+?)(?:;|$)', r'Set-Location \1;', command)
    command = re.sub(r'^cd\s+([^;]+?)(?:;)', r'Set-Location \1;', command)
    return command


async def _auto_promote_to_session(shell: str, command: str, requested_timeout: int) -> str:
    """Spawn a background session for a long-running command and return a JSON handle.

    Used when a sassy_shell timeout_seconds > _MCP_SAFE_TIMEOUT. Running a
    synchronous subprocess.communicate() for longer than the MCP client's
    response wall guarantees the response is dropped on the floor; promoting
    to a session avoids the wedge and gives the caller a poll-able handle.
    """
    from sassymcp.modules.session import start_session_impl
    session_name = f"shell_auto_{uuid.uuid4().hex[:8]}"
    result = await start_session_impl(name=session_name, shell=shell, command=command)
    if result.get("error"):
        return json.dumps({
            "auto_detached": False,
            "error": "Auto-promote to session failed",
            "detail": result["error"],
            "requested_timeout_seconds": requested_timeout,
        }, indent=2)
    return json.dumps({
        "auto_detached": True,
        "session_name": session_name,
        "reason": (
            f"timeout_seconds={requested_timeout}s exceeds the MCP-safe ceiling "
            f"({_MCP_SAFE_TIMEOUT}s). Blocking longer than the MCP client's "
            f"~240s response wall wedges the connection; promoted to a "
            f"background session instead."
        ),
        "hint": (
            f"Poll output with sassy_session_read(name='{session_name}'). "
            f"Send more input with sassy_session_send. "
            f"Stop when done with sassy_session_stop(name='{session_name}')."
        ),
        "command": command,
        "shell": shell,
        "pid": result.get("pid"),
    }, indent=2)


async def _run_subprocess(shell: str, command: str, timeout_seconds: int) -> str:
    """Spawn the configured shell with argv-list form and capture output.

    Used by sassy_shell and by sassy_shell_confirm (which has already
    cleared the interceptor). The argv-list form avoids shell injection;
    the per-shell entry in _SHELL_MAP supplies the right command flag.

    If `timeout_seconds` exceeds _MCP_SAFE_TIMEOUT, the call is auto-promoted
    to a background session via _auto_promote_to_session — see that function
    for the wedge-avoidance rationale.
    """
    if timeout_seconds > _MCP_SAFE_TIMEOUT:
        return await _auto_promote_to_session(shell, command, timeout_seconds)
    timeout_seconds = min(max(timeout_seconds, 1), _MAX_TIMEOUT)
    if shell == "powershell":
        command = _normalize_for_powershell(command)
    cmd = _SHELL_MAP[shell] + [command]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        output = stdout.decode("utf-8", errors="replace").strip()
        errors = stderr.decode("utf-8", errors="replace").strip()
        parts = [f"[exit: {proc.returncode}]"]
        if output: parts.append(output)
        if errors: parts.append(f"STDERR: {errors}")
        return "\n".join(parts)
    except asyncio.TimeoutError:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        return f"Timed out after {timeout_seconds}s"
    except Exception as e:
        return f"Error: {e}"


def _confirm_response(
    command: str, shell: str, tier: str, pattern: str,
    timeout_seconds: int, source: str,
) -> str:
    """Issue a confirm token and return the JSON the caller renders to the user."""
    phrase = None
    if tier == "high":
        # Bind the phrase to the specific pattern so a token issued for
        # "robocopy /mir" cannot be redeemed against a different operation
        # by rephrasing — the phrase has to match exactly.
        phrase = f"I understand this will run a HIGH-risk operation: {pattern}"
    token, _entry = _confirm.make_token(
        command=command, shell=shell, tier=tier, pattern=pattern,
        phrase_required=phrase, timeout_seconds=timeout_seconds,
    )
    _audit.log_pattern_event(
        "pattern_confirm_issued", source, pattern, command,
        {"tier": tier, "token": token, "phrase_required": bool(phrase)},
    )
    payload = {
        "status": "confirmation_required",
        "token": token,
        "tier": tier,
        "pattern": pattern,
        "expires_in_seconds": 60,
        "next_step": (
            f"Call sassy_shell_confirm(token={token!r}"
            + (f", confirm_phrase={phrase!r}" if phrase else "")
            + "). Single-use; expires in 60s; bound to the original cwd."
        ),
    }
    if phrase:
        payload["phrase_required"] = phrase
    return json.dumps(payload, indent=2)


def register(server):
    @server.tool()
    async def sassy_shell(
        command: str,
        shell: str = "powershell",
        timeout_seconds: int = 30,
        allow_pattern: str = "",
    ) -> str:
        """Execute a shell command. shell: powershell, cmd, or wsl.
        Automatically normalizes syntax (e.g. && to ; for PowerShell).

        timeout_seconds > 120: auto-promoted to a background session and
        returns a JSON handle ({"auto_detached": true, "session_name": ...}).
        Poll with sassy_session_read; stop with sassy_session_stop. This is
        because synchronous waits past the MCP client's ~240s response wall
        wedge the connection. For known long-running work, prefer calling
        sassy_session_start directly with a memorable name.

        allow_pattern: opt-in escape hatch for power users. When set to a
        specific pattern label (e.g. 'truncate-by-redirect') OR to '*',
        a regex-pattern match with that label is allowed to execute
        instead of being blocked. The bypass is recorded as a
        'pattern_bypass' audit entry. Keyword matches (rm/del/remove-item)
        are NEVER affected by this flag — only regex patterns can be
        opted out, and only one pattern at a time.

        Tiered destructive-action handling: when
        `interceptor.destructiveAction = "confirm"` (config), MEDIUM- and
        HIGH-tier pattern matches return a confirmation_required JSON
        payload with a single-use token instead of hard-blocking. Call
        `sassy_shell_confirm(token)` (HIGH tier also requires a typed
        phrase) to actually run the command. LOW-tier matches always run
        after a log entry. The default action remains "block".
        """
        if shell not in _SHELL_MAP:
            return "Error: unknown shell. Use: powershell, cmd, wsl"

        # Block-list scan with tier awareness — words inside string literals
        # are reported as 'low' so generated scripts containing risky
        # vocabulary as data don't trip the catastrophic-block path.
        ok, blocklist_tier, err = validate_command_tiered(command)
        if not ok:
            if blocklist_tier == "low":
                _audit.log_pattern_event(
                    "blocklist_literal_allowed", "sassy_shell",
                    "blocklist", command, {"detail": err or ""},
                )
                # Fall through — caller's content has the keyword inside
                # a quoted string, not as an executed command.
            else:
                return f"Error: {err}"

        # Intercept delete commands — move targets to staging folder
        is_delete, keyword = detect_delete_intent(command)
        if is_delete:
            # Only auto-stage when we matched a KEYWORD (rm/del/remove-item/etc.).
            # Regex pattern matches (e.g. "truncate-by-redirect", "new-item -force")
            # cannot reliably identify which token is the target, so staging every
            # space-separated word is actively destructive. Block-only for those.
            from sassymcp.modules._security import _DELETE_KEYWORDS
            kw_root = keyword.split(":", 1)[-1]  # strips "encodedcommand:" prefix
            if kw_root in _DELETE_KEYWORDS:
                targets = _parse_delete_targets(command)
                return await _safe_move_to_staging(targets, keyword, command)

            # Pattern match. allow_pattern wildcard / matching label is the
            # explicit opt-in bypass (logged as pattern_bypass). Otherwise
            # tier-based policy applies.
            if allow_pattern and (allow_pattern == keyword or allow_pattern == "*"):
                _audit.log_pattern_event(
                    "pattern_bypass", "sassy_shell", keyword, command,
                    {"allow_pattern": allow_pattern},
                )
                # fall through to execution
            else:
                tier = pattern_tier(keyword)
                if tier == "low":
                    # LOW: log and run — single-file overwrites, copy /y, etc.
                    _audit.log_pattern_event(
                        "pattern_low_allowed", "sassy_shell", keyword, command,
                        {"tier": "low"},
                    )
                    # fall through to execution
                else:
                    action = str(_cfg("interceptor.destructiveAction", "block")).lower()
                    if action == "confirm":
                        return _confirm_response(
                            command, shell, tier, keyword,
                            timeout_seconds, source="sassy_shell",
                        )
                    # Default — preserve v1.3.x block behavior.
                    _audit.log_pattern_event(
                        "pattern_block", "sassy_shell", keyword, command,
                        {"tier": tier},
                    )
                    return (
                        f"Command blocked (safety): matched destructive pattern "
                        f"'{keyword}' (tier={tier}). No files were moved.\n"
                        f"Options:\n"
                        f"  - retry with allow_pattern='{keyword}' (audited as pattern_bypass)\n"
                        f"  - use sassy_safe_delete() / sassy_write_file() for the intended action\n"
                        f"  - or set interceptor.destructiveAction='confirm' to switch to "
                        f"the confirm-token flow.\n"
                        f"Review recent false-positive candidates with "
                        f"sassy_audit_false_positives()."
                    )

        return await _run_subprocess(shell, command, timeout_seconds)

    @server.tool()
    async def sassy_shell_confirm(token: str, confirm_phrase: str = "") -> str:
        """Execute a sassy_shell command previously returned as confirmation_required.

        Tokens are single-use, expire after 60s, and are bound to the exact
        command + shell + cwd that produced them. HIGH-tier tokens also
        require `confirm_phrase` to match the phrase shown in the original
        confirmation_required response — replays against a different
        command are rejected.
        """
        ok, entry, err = _confirm.consume_token(token, confirm_phrase)
        if not ok:
            _audit.log_pattern_event(
                "pattern_confirm_rejected", "sassy_shell_confirm",
                "(unknown)", "(token consumption)", {"error": err or ""},
            )
            return f"Error: {err}"

        _audit.log_pattern_event(
            "pattern_confirm_executed", "sassy_shell_confirm",
            entry["pattern"], entry["command"],
            {"tier": entry["tier"]},
        )
        return await _run_subprocess(entry["shell"], entry["command"], entry["timeout_seconds"])
