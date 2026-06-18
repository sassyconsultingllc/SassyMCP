"""Centralized filesystem paths for SassyMCP per-user state.

Single source of truth for the per-user state directory. Honors the
SASSYMCP_HOME environment variable so multiple SassyMCP instances can
coexist on the same machine without clobbering each other's persona,
audit log, license, tokens, crosslink DB, or self-signed cert.

Default (unchanged): ~/.sassymcp/
Override:            $SASSYMCP_HOME=/path/to/dir   (~ / $env: expanded)

Importing this module is side-effect-free except for reading os.environ;
the resolved HOME is cached at module load time, so subsequent changes
to SASSYMCP_HOME within the same process are not picked up. This matches
the existing semantics of every module that previously hard-coded
`Path.home() / ".sassymcp"` at module load.
"""
from __future__ import annotations

import os
from pathlib import Path


def _resolve_home() -> Path:
    override = os.environ.get("SASSYMCP_HOME")
    if override:
        return Path(os.path.expandvars(override)).expanduser()
    return Path.home() / ".sassymcp"


# Resolved once at import. Callers should `from sassymcp._paths import HOME`
# (or `from sassymcp import _paths; _paths.HOME`) and treat HOME as immutable
# for the process lifetime.
HOME: Path = _resolve_home()

# Common files inside HOME — exposed as module-level constants for parity
# with the prior `_PERSONA_FILE = Path.home() / ".sassymcp" / "persona.md"`
# pattern. Modules can also just compute `HOME / "whatever"` themselves.
PERSONA_FILE: Path = HOME / "persona.md"
CONFIG_FILE: Path = HOME / "config.json"
TOKENS_FILE: Path = HOME / "tokens.json"
LICENSE_FILE: Path = HOME / "license.json"
AUDIT_LOG: Path = HOME / "audit.log"
CROSSLINK_DB: Path = HOME / "crosslink.db"
SSL_CERT: Path = HOME / "server.crt"
SSL_KEY: Path = HOME / "server.key"

# Process supervisor (sassymcp supervise) — single-instance pidfile, the
# on-disk child registry (crash-survivable source of truth for "what did I
# spawn"), and a transient command channel the running supervisor polls.
# HOME-scoped so $SASSYMCP_HOME instances never fight over one tree.
SUPERVISOR_PIDFILE: Path = HOME / "supervisor.pid"
SUPERVISOR_REGISTRY: Path = HOME / "supervisor-children.json"
SUPERVISOR_CMD: Path = HOME / "supervisor.cmd"

# Tool-usage analytics, runtime config, recent calls, etc., that other
# modules manage with their own filenames inside HOME — they should
# build paths via `HOME / "..."` directly rather than getting an entry
# here, to keep this file from sprawling.

__all__ = [
    "HOME",
    "PERSONA_FILE",
    "CONFIG_FILE",
    "TOKENS_FILE",
    "LICENSE_FILE",
    "AUDIT_LOG",
    "CROSSLINK_DB",
    "SSL_CERT",
    "SSL_KEY",
    "SUPERVISOR_PIDFILE",
    "SUPERVISOR_REGISTRY",
    "SUPERVISOR_CMD",
]
