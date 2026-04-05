# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for SassyMCP — single-file .exe with all modules."""

import os
import sys
from pathlib import Path

block_cipher = None
project_root = os.path.dirname(os.path.abspath(SPEC))

a = Analysis(
    [os.path.join(project_root, 'sassymcp', 'server.py')],
    pathex=[project_root],
    binaries=[],
    datas=[],
    hiddenimports=[
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
        'PIL.ImageGrab',
        'pyautogui',
        'pywinauto',
        'pydantic',
        'pydantic.main',
        'cryptography',
        'cryptography.x509',
        'cryptography.hazmat.primitives',
        'cryptography.hazmat.primitives.asymmetric',
        'cryptography.hazmat.primitives.serialization',
        'cryptography.hazmat.primitives.hashes',
        'cryptography.hazmat.backends',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'numpy.testing'],
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
