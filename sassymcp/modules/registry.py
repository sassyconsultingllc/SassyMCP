"""Registry - Windows Registry read/write for forensics."""

import asyncio


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
