"""SassyMCP Self-Modification — Edit the MCP's own code while it's running.

Two-tier architecture:
  Module files (sassymcp/modules/*.py):
    Edit → git backup → syntax validate → hot-reload (importlib.reload + re-register)
    Zero downtime. Immediate effect.

  Core files (server.py, auth.py, _tool_loader.py, etc.):
    Edit → git backup → syntax validate → flag restart pending
    Call sassy_selfmod_restart() when ready for graceful self-restart.

Every edit auto-commits the pre-edit state for rollback safety.
"""

import asyncio
import importlib
import json
import logging
import os
import py_compile
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("sassymcp.selfmod")

# Package root: parent of sassymcp/ directory
_PKG_DIR = Path(__file__).resolve().parent.parent  # sassymcp/
_PROJECT_ROOT = _PKG_DIR.parent  # the repo root containing sassymcp/
_MODULES_DIR = _PKG_DIR / "modules"

# Files that CAN be hot-reloaded (modules with register())
_HOT_RELOADABLE_DIR = _MODULES_DIR

# Track core edits that need a restart
_pending_restart: list[dict] = []


def _safe_relative(path: Path) -> str:
    """Get path relative to project root, or absolute path as fallback."""
    try:
        return str(path.resolve().relative_to(_PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())

# Track module reload history
_reload_history: list[dict] = []


def _is_module_file(path: Path) -> bool:
    """Check if a path is a hot-reloadable module file."""
    try:
        resolved = path.resolve()
        return (
            resolved.parent == _HOT_RELOADABLE_DIR.resolve()
            and resolved.suffix == ".py"
            and not resolved.name.startswith("_")
        )
    except (OSError, ValueError):
        return False


def _is_infra_file(path: Path) -> bool:
    """Check if a path is an infrastructure helper (_prefixed in modules/)."""
    try:
        resolved = path.resolve()
        return (
            resolved.parent == _HOT_RELOADABLE_DIR.resolve()
            and resolved.suffix == ".py"
            and resolved.name.startswith("_")
            and resolved.name != "__init__.py"
        )
    except (OSError, ValueError):
        return False


def _is_core_file(path: Path) -> bool:
    """Check if a path is a core file (server.py, auth.py, __init__.py)."""
    try:
        resolved = path.resolve()
        return resolved.parent == _PKG_DIR.resolve() and resolved.suffix == ".py"
    except (OSError, ValueError):
        return False


def _resolve_path(rel_path: str) -> Path:
    """Resolve a path relative to the project root.

    Accepts:
      - 'sassymcp/modules/shell.py'
      - 'modules/shell.py' (shorthand for sassymcp/modules/)
      - 'server.py' (shorthand for sassymcp/server.py)
      - Absolute paths within the project

    Security: ALL resolved paths are validated to be within _PROJECT_ROOT.
    Raises ValueError on traversal attempts.
    """
    p = Path(rel_path)
    project_root = _PROJECT_ROOT.resolve()

    def _validate(candidate: Path) -> Path:
        """Ensure resolved path is within the project root."""
        resolved = candidate.resolve()
        try:
            resolved.relative_to(project_root)
        except ValueError:
            raise ValueError(
                f"Path '{rel_path}' resolves to '{resolved}' which is outside "
                f"the SassyMCP project root '{project_root}'"
            )
        return candidate

    # Absolute path
    if p.is_absolute():
        return _validate(p)

    # Try as-is from project root first
    candidate = _PROJECT_ROOT / p
    if candidate.resolve().parts and candidate.exists():
        return _validate(candidate)

    # Shorthand: 'modules/shell.py' → 'sassymcp/modules/shell.py'
    candidate = _PKG_DIR / p
    if candidate.exists():
        return _validate(candidate)

    # Shorthand: 'shell.py' → 'sassymcp/modules/shell.py'
    candidate = _MODULES_DIR / p
    if candidate.exists():
        return _validate(candidate)

    # Last try: sassymcp dir for things like 'server.py' (new files)
    candidate = _PKG_DIR / p
    return _validate(candidate)


def _git_backup(filepath: Path, message: str) -> dict:
    """Auto-commit the current state of a file for rollback safety.

    Returns {"backed_up": True/False, "commit": "sha"/None, "note": "..."}
    """
    try:
        # Check if we're in a git repo
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(_PROJECT_ROOT),
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return {"backed_up": False, "commit": None, "note": "Not a git repository"}

        # Check if file has uncommitted changes worth backing up
        result = subprocess.run(
            ["git", "diff", "--name-only", str(filepath.resolve())],
            cwd=str(_PROJECT_ROOT),
            capture_output=True, text=True, timeout=5,
        )
        # Also check staged changes
        result_staged = subprocess.run(
            ["git", "diff", "--staged", "--name-only", str(filepath.resolve())],
            cwd=str(_PROJECT_ROOT),
            capture_output=True, text=True, timeout=5,
        )
        has_changes = bool(result.stdout.strip() or result_staged.stdout.strip())

        if not has_changes:
            # Check if the file is untracked
            result = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard", str(filepath.resolve())],
                cwd=str(_PROJECT_ROOT),
                capture_output=True, text=True, timeout=5,
            )
            if not result.stdout.strip():
                return {"backed_up": False, "commit": None, "note": "No changes to back up"}

        # Stage just this file
        subprocess.run(
            ["git", "add", str(filepath.resolve())],
            cwd=str(_PROJECT_ROOT),
            capture_output=True, text=True, timeout=5,
        )

        # Commit with selfmod prefix
        result = subprocess.run(
            ["git", "commit", "-m", message, "--", str(filepath.resolve())],
            cwd=str(_PROJECT_ROOT),
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            # Might fail if nothing to commit after all
            return {"backed_up": False, "commit": None, "note": result.stderr.strip()[:200]}

        # Get the commit SHA
        sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_PROJECT_ROOT),
            capture_output=True, text=True, timeout=5,
        )
        sha = sha_result.stdout.strip()[:12]

        return {"backed_up": True, "commit": sha, "note": message}

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return {"backed_up": False, "commit": None, "note": f"Git error: {e}"}


def _syntax_check(filepath: Path) -> tuple[bool, str]:
    """Validate Python syntax. Returns (ok, error_message)."""
    try:
        py_compile.compile(str(filepath), doraise=True)
        return True, ""
    except py_compile.PyCompileError as e:
        return False, str(e)


def _hot_reload_module(server, module_name: str) -> dict:
    """Hot-reload a module: reimport, re-register, re-wrap audit.

    Returns status dict.
    """
    full_name = f"sassymcp.modules.{module_name}"

    try:
        # Snapshot tools before
        before = set()
        if hasattr(server, "_tool_manager"):
            before = set(server._tool_manager._tools.keys())

        # Reload the module
        if full_name in sys.modules:
            mod = importlib.reload(sys.modules[full_name])
        else:
            mod = importlib.import_module(full_name)

        # Re-register tools
        if hasattr(mod, "register"):
            mod.register(server)
        else:
            return {"reloaded": False, "error": f"Module {module_name} has no register() function"}

        # Find newly registered/updated tools
        after = set()
        if hasattr(server, "_tool_manager"):
            after = set(server._tool_manager._tools.keys())

        new_tools = after - before

        # Re-wrap audit middleware on touched tools (prevent double-wrapping via flag)
        from sassymcp.server import audit_tool
        rewrapped = 0
        if hasattr(server, "_tool_manager"):
            for tool_name in after:
                tool = server._tool_manager._tools.get(tool_name)
                if tool and hasattr(tool, "fn"):
                    # Only re-wrap if NOT already wrapped, or if it's a tool from this module
                    if not getattr(tool.fn, "_audit_wrapped", False):
                        from sassymcp.modules._tool_loader import validate_tool
                        validate_tool(tool.fn)
                        tool.fn = audit_tool(tool.fn)
                        tool.fn._audit_wrapped = True
                        rewrapped += 1

        # Update tool-to-group mapping
        from sassymcp.modules._tool_loader import register_tool_group
        for tool_name in new_tools:
            register_tool_group(tool_name, module_name)

        _reload_history.append({
            "module": module_name,
            "timestamp": time.time(),
            "new_tools": list(new_tools),
            "rewrapped": rewrapped,
        })

        return {
            "reloaded": True,
            "module": module_name,
            "new_tools": list(new_tools),
            "total_tools_after": len(after),
            "rewrapped": rewrapped,
        }

    except Exception as e:
        return {"reloaded": False, "module": module_name, "error": str(e)}


def register(server):
    """Register self-modification tools."""

    @server.tool()
    async def sassy_selfmod_read(path: str) -> str:
        """Read a SassyMCP source file.

        Accepts relative paths:
          'modules/shell.py' or 'shell.py' → sassymcp/modules/shell.py
          'server.py' → sassymcp/server.py
          'sassymcp/auth.py' → sassymcp/auth.py
        """
        try:
            resolved = _resolve_path(path)
        except ValueError as e:
            return json.dumps({"error": str(e)})

        if not resolved.exists():
            return json.dumps({"error": f"File not found: {resolved}"})
        if not resolved.suffix == ".py":
            return json.dumps({"error": "Only .py files can be read through selfmod"})

        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            numbered = [f"{i+1:>4} | {line}" for i, line in enumerate(lines)]
            return json.dumps({
                "path": str(resolved),
                "lines": len(lines),
                "relative": _safe_relative(resolved),
                "reloadable": _is_module_file(resolved),
                "content": "\n".join(numbered),
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_selfmod_edit(path: str, old_text: str, new_text: str) -> str:
        """Surgical self-edit: find old_text, replace with new_text.

        Auto git-backup → edit → syntax check → hot-reload (if module).
        If syntax check fails, the edit is reverted automatically.

        For core files: edit succeeds but flags restart as pending.
        """
        try:
            resolved = _resolve_path(path)
        except ValueError as e:
            return json.dumps({"error": str(e)})

        if not resolved.exists():
            return json.dumps({"error": f"File not found: {resolved}"})
        if resolved.suffix != ".py":
            return json.dumps({"error": "Only .py files can be edited through selfmod"})

        # Read current content
        try:
            content = resolved.read_text(encoding="utf-8")
        except Exception as e:
            return json.dumps({"error": f"Read failed: {e}"})

        # Verify match
        count = content.count(old_text)
        if count == 0:
            return json.dumps({"error": "old_text not found in file", "path": str(resolved)})
        if count > 1:
            return json.dumps({
                "error": f"old_text matches {count} locations. Include more context to make it unique.",
                "path": str(resolved),
            })

        # Git backup
        rel_path = _safe_relative(resolved)
        backup = _git_backup(resolved, f"selfmod: backup {rel_path} before edit")

        # Apply edit
        new_content = content.replace(old_text, new_text, 1)
        resolved.write_text(new_content, encoding="utf-8")

        # Syntax check
        ok, err = _syntax_check(resolved)
        if not ok:
            # Revert
            resolved.write_text(content, encoding="utf-8")
            return json.dumps({
                "error": "Syntax error — edit reverted",
                "syntax_error": err,
                "backup": backup,
            })

        result = {
            "edited": str(resolved),
            "relative": rel_path,
            "backup": backup,
            "syntax_valid": True,
        }

        # Hot-reload if module file
        if _is_module_file(resolved):
            module_name = resolved.stem
            reload_result = _hot_reload_module(server, module_name)
            result["reload"] = reload_result
            result["restart_required"] = False
            logger.info(f"Self-edit + hot-reload: {module_name}")
        elif _is_infra_file(resolved):
            # Infrastructure helpers can be reimported but may need dependents reloaded
            module_name = resolved.stem
            full_name = f"sassymcp.modules.{module_name}"
            try:
                if full_name in sys.modules:
                    importlib.reload(sys.modules[full_name])
                    result["reload"] = {"reloaded": True, "module": module_name, "note": "Infra helper reloaded. Dependent modules may need reload too."}
                else:
                    result["reload"] = {"reloaded": False, "note": "Module not currently imported"}
            except Exception as e:
                result["reload"] = {"reloaded": False, "error": str(e)}
            result["restart_required"] = False
        else:
            # Core file — can't hot-reload
            _pending_restart.append({
                "file": rel_path,
                "timestamp": time.time(),
                "backup_commit": backup.get("commit"),
            })
            result["restart_required"] = True
            result["note"] = "Core file edited. Call sassy_selfmod_restart() to apply."
            logger.info(f"Self-edit (core, restart pending): {rel_path}")

        return json.dumps(result, indent=2)

    @server.tool()
    async def sassy_selfmod_write(path: str, content: str) -> str:
        """Full file replacement with git backup + syntax check + reload.

        Use sassy_selfmod_edit for surgical changes. This replaces the ENTIRE file.
        """
        try:
            resolved = _resolve_path(path)
        except ValueError as e:
            return json.dumps({"error": str(e)})

        if resolved.suffix != ".py":
            return json.dumps({"error": "Only .py files can be written through selfmod"})

        # Git backup if file exists
        backup = {"backed_up": False, "note": "new file"}
        if resolved.exists():
            rel_path = _safe_relative(resolved)
            backup = _git_backup(resolved, f"selfmod: backup {rel_path} before rewrite")
            old_content = resolved.read_text(encoding="utf-8")
        else:
            old_content = None
            rel_path = _safe_relative(resolved)

        # Ensure parent directory exists
        resolved.parent.mkdir(parents=True, exist_ok=True)

        # Write new content
        resolved.write_text(content, encoding="utf-8")

        # Syntax check
        ok, err = _syntax_check(resolved)
        if not ok:
            # Revert
            if old_content is not None:
                resolved.write_text(old_content, encoding="utf-8")
            else:
                resolved.unlink(missing_ok=True)
            return json.dumps({
                "error": "Syntax error — write reverted",
                "syntax_error": err,
                "backup": backup,
            })

        result = {
            "written": str(resolved),
            "relative": rel_path,
            "lines": len(content.splitlines()),
            "backup": backup,
            "syntax_valid": True,
        }

        # Hot-reload if module
        if _is_module_file(resolved):
            module_name = resolved.stem
            reload_result = _hot_reload_module(server, module_name)
            result["reload"] = reload_result
            result["restart_required"] = False
        elif _is_infra_file(resolved):
            # Infra helpers can be reimported (same behavior as edit)
            module_name = resolved.stem
            full_name = f"sassymcp.modules.{module_name}"
            try:
                if full_name in sys.modules:
                    importlib.reload(sys.modules[full_name])
                    result["reload"] = {"reloaded": True, "module": module_name, "note": "Infra helper reloaded. Dependent modules may need reload too."}
                else:
                    result["reload"] = {"reloaded": False, "note": "Module not currently imported"}
            except Exception as e:
                result["reload"] = {"reloaded": False, "error": str(e)}
            result["restart_required"] = False
        elif _is_core_file(resolved):
            _pending_restart.append({
                "file": rel_path,
                "timestamp": time.time(),
                "backup_commit": backup.get("commit"),
            })
            result["restart_required"] = True
            result["note"] = "Call sassy_selfmod_restart() to apply core changes."

        return json.dumps(result, indent=2)

    @server.tool()
    async def sassy_selfmod_reload(module_name: str) -> str:
        """Force hot-reload a module without editing it.

        module_name: e.g. 'shell', 'fileops', 'utility'
        Useful after external edits or to pick up changes from infra files.
        """
        mod_file = _MODULES_DIR / f"{module_name}.py"
        if not mod_file.exists():
            return json.dumps({"error": f"Module not found: {module_name}"})
        if module_name.startswith("_"):
            return json.dumps({"error": "Cannot reload infrastructure modules directly. Reload dependent modules instead."})

        result = _hot_reload_module(server, module_name)
        return json.dumps(result, indent=2)

    @server.tool()
    async def sassy_selfmod_restart(delay_seconds: float = 1.0) -> str:
        """Graceful self-restart: spawn new SassyMCP process, then exit.

        The MCP client (Claude Desktop, Grok, etc.) reconnects automatically.
        All pending core changes take effect after restart.

        delay_seconds: wait before killing old process (lets response reach client)
        """
        # Reconstruct the launch command
        # sys.argv has the original arguments, sys.executable has the Python path
        exe = sys.executable
        argv = sys.argv[:]

        # If running via 'uv run sassymcp', reconstruct that
        # Otherwise use python -m sassymcp.server
        import shutil
        uv_path = shutil.which("uv")

        # Determine how we were launched
        if any("sassymcp" in a for a in argv):
            # Direct invocation: python -m sassymcp.server or sassymcp CLI
            new_cmd = [exe] + argv
        elif uv_path:
            # Try uv run
            new_cmd = [uv_path, "run", "sassymcp"] + argv[1:]
        else:
            new_cmd = [exe, "-m", "sassymcp.server"] + argv[1:]

        pending = list(_pending_restart)
        _pending_restart.clear()

        logger.info(f"Self-restart initiated. Pending changes: {len(pending)}")
        logger.info(f"Restart command: {' '.join(new_cmd)}")

        # Prepare response before restarting
        result = {
            "status": "restarting",
            "pending_changes": pending,
            "restart_command": " ".join(new_cmd),
            "delay_seconds": delay_seconds,
        }

        # Schedule the restart after a delay (so the response can reach the client)
        async def _do_restart():
            await asyncio.sleep(delay_seconds)
            try:
                # Spawn new process (detached)
                if os.name == "nt":
                    # Windows: CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS
                    subprocess.Popen(
                        new_cmd,
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                        close_fds=True,
                    )
                else:
                    subprocess.Popen(
                        new_cmd,
                        start_new_session=True,
                        close_fds=True,
                    )
                logger.info("New SassyMCP process spawned. Running graceful shutdown then exiting.")
                # Run graceful shutdown (crosslink notification, state cleanup)
                try:
                    from sassymcp.server import _graceful_shutdown
                    await _graceful_shutdown(signum="selfmod-restart")
                except Exception:
                    pass
                await asyncio.sleep(0.3)
                os._exit(0)
            except Exception as e:
                logger.error(f"Self-restart failed: {e}")

        asyncio.create_task(_do_restart())

        return json.dumps(result, indent=2)

    @server.tool()
    async def sassy_selfmod_status() -> str:
        """Show self-modification status: pending restarts, reload history, file index."""
        # List all editable files
        module_files = sorted([
            f.name for f in _MODULES_DIR.glob("*.py")
            if not f.name.startswith("_") and f.name != "__init__.py"
        ])
        infra_files = sorted([
            f.name for f in _MODULES_DIR.glob("_*.py")
            if f.name != "__init__.py"
        ])
        core_files = sorted([
            f.name for f in _PKG_DIR.glob("*.py")
        ])

        # Git status of sassymcp/ (porcelain + ignore untracked to avoid hangs on large repos)
        git_status = ""
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no", "--", "sassymcp/"],
                cwd=str(_PROJECT_ROOT),
                capture_output=True, text=True, timeout=10,
            )
            git_status = result.stdout.strip() if result.returncode == 0 else "(git error)"
        except subprocess.TimeoutExpired:
            git_status = "(git status timed out)"
        except Exception:
            git_status = "(git not available)"

        return json.dumps({
            "pending_restart": _pending_restart,
            "restart_required": len(_pending_restart) > 0,
            "reload_history": _reload_history[-20:],
            "editable_files": {
                "modules (hot-reloadable)": module_files,
                "infrastructure": infra_files,
                "core (restart required)": core_files,
            },
            "git_status": git_status,
            "project_root": str(_PROJECT_ROOT),
        }, indent=2)

    @server.tool()
    async def sassy_selfmod_rollback(path: str) -> str:
        """Revert a file to its last git-committed state.

        Discards ALL uncommitted changes to the file.
        If the file was backed up by selfmod before editing, the backup commit is preserved.
        """
        try:
            resolved = _resolve_path(path)
        except ValueError as e:
            return json.dumps({"error": str(e)})

        rel_path = _safe_relative(resolved)

        try:
            result = subprocess.run(
                ["git", "checkout", "HEAD", "--", rel_path],
                cwd=str(_PROJECT_ROOT),
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return json.dumps({"error": f"Git checkout failed: {result.stderr.strip()}"})

            # If this was a module file, hot-reload the reverted version
            reload_result = None
            if _is_module_file(resolved):
                module_name = resolved.stem
                reload_result = _hot_reload_module(server, module_name)

            # Remove from pending restart if applicable
            _pending_restart[:] = [p for p in _pending_restart if p["file"] != rel_path]

            return json.dumps({
                "rolled_back": rel_path,
                "reload": reload_result,
                "pending_restart_cleared": rel_path,
            }, indent=2)

        except Exception as e:
            return json.dumps({"error": str(e)})

    logger.info("Self-modification module loaded (git-backed hot-reload)")
