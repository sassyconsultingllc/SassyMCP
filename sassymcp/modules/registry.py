"""Registry - Windows Registry read/write for forensics."""

import asyncio

from sassymcp.modules._security import validate_path as _validate_path, is_protected_path as _is_protected_path
from pathlib import Path


def _register_hooks():
    from sassymcp.modules._hooks import register_hook
    register_hook(
        name="windows_forensics",
        module="registry",
        description="Windows Registry forensics — autorun persistence, recent activity, installed software, USB history.",
        triggers=[
            "registry", "regedit", "autorun", "auto-run", "auto run",
            "persistence", "persist", "startup", "boot", "scheduled task",
            "what runs on", "rootkit", "forensic", "forensics", "incident response",
            "USB history", "uninstall", "installed software", "MRU",
        ],
        instructions="""
## Windows Registry Forensics Playbook

Triggered when the user asks about Windows persistence, what's running on
boot, suspicious autorun entries, or generic forensic investigation.

### Triage order
1. **Boot persistence** (highest signal-to-noise) — `sassy_reg_autoruns` returns
   the union of all known autorun keys (Run, RunOnce, RunOnceEx, Image File
   Execution Options, Services, Scheduled Tasks shadow). Look for unsigned
   binaries, paths under %TEMP% or %APPDATA%, names that mimic legit Windows
   processes (svhost.exe vs svchost.exe).
2. **Specific key inspection** — `sassy_reg_read key_path="HKLM\\Software\\..."`
   for ad-hoc reads. Use forward-slash escaping for nested values.
3. **Export for offline analysis** — `sassy_reg_export key_path="..." output_file="..."`
   produces a .reg file you can grep, diff, or share.

### Common forensic targets

| Concern | Key path |
|---|---|
| Generic boot persistence | HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Run |
| Per-user persistence | HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run |
| Service hijacks | HKLM\\System\\CurrentControlSet\\Services |
| Recently-run programs | HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\UserAssist |
| USB device history | HKLM\\System\\CurrentControlSet\\Enum\\USBSTOR |
| Installed software | HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall |
| Image File Execution Options (debugger hijack) | HKLM\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options |

### Don't
- `sassy_reg_write` without explicit user confirmation. Registry corruption
  is a much harder recovery than a deleted file.
- Combine with `sassy_security_audit_*` tools (security_audit module) for
  hash checks on suspicious binaries you find.
""",
    )

try:
    _register_hooks()
except Exception:
    pass

async def _reg(*args, timeout=15):
    """Run reg.exe with explicit args (no shell interpolation)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "reg.exe", *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout.decode("utf-8", errors="replace").strip()
        return out if out else stderr.decode("utf-8", errors="replace").strip()
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return f"Timed out after {timeout}s"
    except FileNotFoundError:
        return "Error: reg.exe not found"


def register(server):
    @server.tool()
    async def sassy_reg_read(key_path: str, value_name: str = "") -> str:
        """Read a Windows registry key or value."""
        if value_name:
            return await _reg("query", key_path, "/v", value_name)
        return await _reg("query", key_path)

    @server.tool()
    async def sassy_reg_write(key_path: str, value_name: str, value_data: str, value_type: str = "REG_SZ") -> str:
        """Write a Windows registry value."""
        valid_types = {"REG_SZ", "REG_DWORD", "REG_QWORD", "REG_EXPAND_SZ", "REG_MULTI_SZ", "REG_BINARY"}
        if value_type not in valid_types:
            return f"Error: invalid type. Use: {', '.join(sorted(valid_types))}"
        return await _reg("add", key_path, "/v", value_name, "/t", value_type, "/d", value_data, "/f")

    @server.tool()
    async def sassy_reg_export(key_path: str, output_file: str) -> str:
        """Export registry key to .reg file."""
        ok, err = _validate_path(output_file)
        if not ok:
            return f"Error: {err}"
        prot, reason = _is_protected_path(Path(output_file).absolute())
        if prot:
            return f"Refused: output_file is protected ({reason})"
        result = await _reg("export", key_path, output_file, "/y", timeout=30)
        if "ERROR" in result.upper() or "unable to find" in result.lower():
            return f"Error: Registry key '{key_path}' not found or access denied."
        return result if result else f"Exported {key_path} to {output_file}"

    @server.tool()
    async def sassy_autorun_entries() -> str:
        """List common autorun/startup registry entries."""
        keys = [
            r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
            r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce",
            r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
            r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce",
        ]
        results = []
        for key in keys:
            out = await _reg("query", key)
            if out and "ERROR" not in out.upper():
                results.append(f"--- {key} ---\n{out}")
        return "\n\n".join(results) if results else "No autorun entries found"
