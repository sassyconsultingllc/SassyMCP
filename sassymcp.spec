# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for SassyMCP — single-file binary with all modules.

Cross-platform: the same spec builds a Windows `sassymcp.exe`, a macOS
`sassymcp` Mach-O, or a Linux ELF. PyInstaller cannot cross-compile — build
each artifact on its own OS (or CI runner). Platform-only dependencies
(pywinauto on Windows, pyobjc on macOS) are added/excluded per build host
below so a Mac build doesn't try to bundle the Windows-only UIA stack.
"""

import os
import sys
from pathlib import Path

block_cipher = None
project_root = os.path.dirname(os.path.abspath(SPEC))

_IS_WIN = sys.platform == 'win32'
_IS_MAC = sys.platform == 'darwin'

# Window control / screen grab stacks differ by OS. Windows uses pywinauto
# (UIA) + PIL.ImageGrab; macOS drives System Events via osascript at runtime
# and reads displays through pyobjc (AppKit/Quartz). _platform is the routing
# head every module imports.
if _IS_WIN:
    _PLATFORM_HIDDEN = ['pywinauto', 'PIL.ImageGrab']
    _PLATFORM_EXCLUDES = []
elif _IS_MAC:
    _PLATFORM_HIDDEN = ['PIL.ImageGrab', 'AppKit', 'Quartz', 'objc',
                        'Foundation', 'CoreFoundation']
    _PLATFORM_EXCLUDES = ['pywinauto']
else:
    _PLATFORM_HIDDEN = []
    _PLATFORM_EXCLUDES = ['pywinauto']

a = Analysis(
    [os.path.join(project_root, 'sassymcp', 'server.py')],
    pathex=[project_root],
    binaries=[],
    datas=[],
    hiddenimports=[
        # License + monetization (v1.6)
        'sassymcp.license',
        'sassymcp._lemonsqueezy',
        'sassymcp._cli_wizard',
        'sassymcp._paths',
        'sassymcp._platform',
        'sassymcp._atomic',
        # Process supervisor (v1.8) — lazy-imported by the `supervise`
        # subcommand, so PyInstaller can't see them without these entries.
        # _jobctl is pure ctypes/stdlib (no pywin32), so nothing else to add.
        'sassymcp.supervisor',
        'sassymcp._jobctl',
        # Core
        'sassymcp.modules.fileops',
        'sassymcp.modules.shell',
        'sassymcp.modules.ui_automation',
        'sassymcp.modules.editor',
        'sassymcp.modules.audit',
        'sassymcp.modules.session',
        'sassymcp.modules.runtime_config',
        'sassymcp.modules.meta',
        'sassymcp.modules._tool_loader',
        # System
        'sassymcp.modules.network_audit',
        'sassymcp.modules.process_manager',
        'sassymcp.modules.security_audit',
        'sassymcp.modules.registry',
        'sassymcp.modules.bluetooth',
        'sassymcp.modules.eventlog',
        'sassymcp.modules.clipboard',
        # Android
        'sassymcp.modules.adb',
        'sassymcp.modules.phone_screen',
        # GitHub
        'sassymcp.modules.github_quick',
        'sassymcp.modules.github_ops',
        'sassymcp.modules._github_client',
        # v020
        'sassymcp.modules.vision',
        'sassymcp.modules.app_launcher',
        'sassymcp.modules.web_inspector',
        'sassymcp.modules.crosslink',
        # Remote Linux SSH
        'sassymcp.modules.linux',
        # Persona / Utility / Setup / Selfmod
        'sassymcp.modules.persona',
        'sassymcp.modules.utility',
        'sassymcp.modules.selfmod',
        'sassymcp.modules.setup_wizard',
        'sassymcp.modules.tools_manager',
        'sassymcp.modules.memory',
        'sassymcp.modules.updater',
        'sassymcp.modules._hooks',
        # Combos + prompts (loaded dynamically via __import__ in server._load_modules;
        # PyInstaller can't see these, so they MUST be listed here or the frozen
        # exe silently logs "Failed to register combos/prompts: No module named ...".
        'sassymcp.modules.combos',
        'sassymcp.modules.prompts',
        'sassymcp.modules._confirm',
        # Infrastructure
        'sassymcp.modules.state_manager',
        'sassymcp.modules.observability',
        'sassymcp.modules._security',
        'sassymcp.modules._rate_limiter',
        'sassymcp.auth',
        # MCP framework
        'mcp',
        'mcp.server',
        'mcp.server.fastmcp',
        'mcp.server.transport_security',
        'mcp.server.sse',
        'mcp.server.streamable_http',
        'mcp.server.auth',
        'mcp.server.auth.provider',
        'mcp.server.auth.settings',
        # Dependencies
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'starlette',
        'starlette.applications',
        'starlette.routing',
        'starlette.responses',
        'starlette.requests',
        'anyio',
        'anyio._backends',
        'anyio._backends._asyncio',
        'httpx',
        'httpcore',
        'psutil',
        'PIL',
        'PIL.Image',
        'pyautogui',
        'pydantic',
        'pydantic.main',
        'cryptography',
        'cryptography.x509',
        'cryptography.hazmat.primitives',
        'cryptography.hazmat.primitives.asymmetric',
        'cryptography.hazmat.primitives.serialization',
        'cryptography.hazmat.primitives.hashes',
        'cryptography.hazmat.backends',
    ] + _PLATFORM_HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'scipy', 'numpy.testing',
        # Optional extras — lazy-imported by vision/web_inspector with
        # graceful "install X to enable" fallback messages. Excluded from
        # the shipped exe to keep it lean; users who need OCR or
        # playwright screenshots install them into their own Python.
        'playwright', 'playwright.sync_api', 'playwright.async_api',
        'pytesseract',
        'watchdog',          # SASSYMCP_DEV=1 live-reload only
    ] + _PLATFORM_EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='sassymcp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
