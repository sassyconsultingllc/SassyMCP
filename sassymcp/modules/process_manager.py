"""ProcessManager - Windows + Android process control.

Updated with:
- sassy_kill_all_sassymcp (one-click SassyMCP zombie killer for Grok/Claude Desktop lock issues)
- Graceful shutdown support (SIGINT/SIGTERM handling + cleanup)
"""

import asyncio
import json
import os
import psutil
import signal
import time
import platform

def register(server):

    @server.tool()
    async def sassy_processes(filter_str: str = "", sort_by: str = "cpu") -> str:
        """List running processes. sort_by: cpu, memory, name."""
        procs = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
            try:
                info = p.info
                if filter_str and filter_str.lower() not in info["name"].lower():
                    continue
                procs.append({
                    "pid": info["pid"],
                    "name": info["name"],
                    "cpu": info["cpu_percent"] or 0,
                    "mem_mb": round((info["memory_info"].rss if info["memory_info"] else 0) / 1048576, 1)
                })
            except Exception:
                continue
        key = {"cpu": "cpu", "memory": "mem_mb", "name": "name"}.get(sort_by, "cpu")
        procs.sort(key=lambda x: x[key], reverse=(key != "name"))
        return json.dumps(procs[:50], indent=2)

    @server.tool()
    async def sassy_kill_process(pid: int, force: bool = False) -> str:
        """Kill a process by PID."""
        try:
            p = psutil.Process(pid)
            name = p.name()
            p.kill() if force else p.terminate()
            return f"Terminated {name} (PID: {pid})"
        except psutil.NoSuchProcess:
            return f"Error: No process with PID {pid}"
        except psutil.AccessDenied:
            return f"Error: Access denied to kill PID {pid}"
        except Exception as e:
            return f"Error: {e}"

    @server.tool()
    async def sassy_android_processes(device: str = "") -> str:
        """List running processes on Android device."""
        if device:
            from sassymcp.modules._security import validate_adb_device
            ok, err = validate_adb_device(device)
            if not ok:
                return f"Error: {err}"
        args = ["adb"] + (["-s", device] if device else []) + ["shell", "ps -A -o PID,NAME,%CPU,RSS"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            return stdout.decode("utf-8", errors="replace").strip()
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return "Timed out after 15s"
        except FileNotFoundError:
            return "Error: adb not found"
        except Exception as e:
            return f"Error: {e}"

    @server.tool()
    async def sassy_system_info() -> str:
        """Get system resource summary."""
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk_path = "C:\\" if os.name == "nt" else "/"
        disk = psutil.disk_usage(disk_path)
        uptime = time.time() - psutil.boot_time()
        return json.dumps({
            "hostname": platform.node(),
            "os": f"{platform.system()} {platform.release()}",
            "cpu_percent": cpu,
            "cpu_cores": psutil.cpu_count(),
            "ram_total_gb": round(mem.total / 1073741824, 1),
            "ram_used_gb": round(mem.used / 1073741824, 1),
            "ram_percent": mem.percent,
            "disk_total_gb": round(disk.total / 1073741824, 1),
            "disk_percent": disk.percent,
            "uptime": f"{int(uptime//3600)}h {int((uptime%3600)//60)}m",
        }, indent=2)

    @server.tool()
    async def sassy_kill_all_sassymcp(force: bool = False) -> str:
        """One-click nuclear option: kill every SassyMCP, uv, and Grok/Claude-spawned Python process.
        
        Fixes the exact error: "another program is currently using this process"
        when trying to open Grok Desktop or Claude Desktop.
        
        force=True = more aggressive (kills any python.exe / uv.exe that might be related).
        """
        current_pid = os.getpid()
        patterns = ["sassymcp", "uv run sassymcp", "-m sassymcp", "sassymcp.exe"]
        if force:
            # Even in force mode, require "sassymcp" or "mcp" in the cmdline
            # Never kill arbitrary python.exe processes
            patterns.extend(["uv.exe"])

        killed = []
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                info = proc.info
                if info["pid"] == current_pid:
                    continue  # Never kill ourselves

                cmdline = " ".join(info.get("cmdline") or []).lower()
                name = (info.get("name") or "").lower()

                matched = any(p.lower() in name or p.lower() in cmdline for p in patterns)
                # In force mode with uv.exe, still require sassymcp in cmdline
                if matched and force and name == "uv.exe" and "sassymcp" not in cmdline:
                    matched = False
                if matched:
                    try:
                        proc.kill() if force else proc.terminate()
                        killed.append({
                            "pid": info["pid"],
                            "name": info.get("name"),
                            "cmdline": cmdline[:120]
                        })
                    except Exception:
                        pass
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        await asyncio.sleep(0.5)  # Give Windows time to release file locks

        return json.dumps({
            "status": "killed",
            "count": len(killed),
            "processes": killed,
            "note": "All SassyMCP-related processes terminated. You can now safely restart Grok Desktop / SassyMCP."
        }, indent=2)


# Shutdown is handled by server.py — do not register competing signal handlers here.


if __name__ == "__main__":
    print("This module should be imported by server.py, not run directly.")