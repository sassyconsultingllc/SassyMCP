# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-O56YZELDYXKF
"""HTTP bind resolution for SassyMCP.

Single source of truth for the (host, port, ssl) the HTTP/SSE server binds
to. Precedence, highest first:

  1. CLI flags: ``--host`` / ``--port`` / ``--ssl``
  2. Environment: ``SASSYMCP_HOST`` / ``SASSYMCP_PORT`` / ``SASSYMCP_SSL``
  3. Config file (``$SASSYMCP_HOME/config.json``):
     ``http.host`` / ``http.port`` / ``http.ssl``
  4. Built-in defaults: ``127.0.0.1`` / ``21001`` / no SSL

The resolver pre-parses *only* the bind flags (``parse_known_args``) so it
can run at import time — before the server's full argparse — which lets the
OAuth protected-resource metadata advertise the exact URL clients must
match. (A stale default here once caused the classic
``localhost``-vs-``127.0.0.1`` exact-match failure against Claude Code.)

Only the standard library is imported at module top level so this module
can be used from ``server.py``, ``install.py`` and ``supervisor.py``
without import cycles; the runtime config is imported lazily inside
:func:`resolve_http_bind`.
"""

import argparse
import os
import sys
from typing import NamedTuple, Optional, Sequence

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 21001


class BindConfigError(ValueError):
    """Raised when a configured bind value (host/port) is invalid."""


class HttpBind(NamedTuple):
    host: str
    port: int
    ssl: bool

    @property
    def scheme(self) -> str:
        return "https" if self.ssl else "http"

    @property
    def url(self) -> str:
        """Base URL of the server, e.g. ``http://127.0.0.1:21001``."""
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def mcp_url(self) -> str:
        """Streamable-HTTP endpoint URL clients connect to."""
        return f"{self.url}/mcp"


def _preparse_cli(argv: Optional[Sequence[str]]):
    """Extract just --host/--port/--ssl from an argv list, ignoring the rest."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    # store_true with default None gives a tri-state: None = flag absent.
    parser.add_argument("--ssl", dest="ssl", action="store_true", default=None)
    args, _ = parser.parse_known_args(argv)
    return args


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _coerce_port(value, source: str) -> int:
    """Validate a port value from env/config/CLI; raise BindConfigError if bad."""
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        raise BindConfigError(
            f"Invalid port {value!r} from {source}: must be an integer 1-65535"
        )
    if not 1 <= port <= 65535:
        raise BindConfigError(
            f"Invalid port {port} from {source}: must be an integer 1-65535"
        )
    return port


def resolve_http_bind(argv: Optional[Sequence[str]] = None) -> HttpBind:
    """Resolve the effective HTTP bind.

    ``argv``: argument list to pre-parse for --host/--port/--ssl.
    ``None`` (default) uses ``sys.argv[1:]``. Pass ``[]`` to resolve purely
    from environment + config file (used by installers/supervisors that are
    not the server process itself).
    """
    cli = _preparse_cli(sys.argv[1:] if argv is None else argv)

    # runtime_config is imported lazily: server.py imports this module at
    # its top, before its own heavy imports have run.
    from sassymcp.modules import runtime_config as _rc

    # ── host ──────────────────────────────────────────────────────────
    host = (
        (cli.host or "").strip()
        or os.environ.get("SASSYMCP_HOST", "").strip()
        or str(_rc.get("http.host", "") or "").strip()
        or DEFAULT_HOST
    )

    # ── port ──────────────────────────────────────────────────────────
    if cli.port is not None:
        port = _coerce_port(cli.port, "--port")
    elif os.environ.get("SASSYMCP_PORT", "").strip():
        port = _coerce_port(os.environ["SASSYMCP_PORT"].strip(), "SASSYMCP_PORT")
    elif _rc.get("http.port", "") not in ("", None):
        port = _coerce_port(_rc.get("http.port"), "config.json http.port")
    else:
        port = DEFAULT_PORT

    # ── ssl ───────────────────────────────────────────────────────────
    if cli.ssl is not None:
        ssl = cli.ssl
    elif os.environ.get("SASSYMCP_SSL", "").strip():
        ssl = _truthy(os.environ["SASSYMCP_SSL"])
    else:
        _cfg_ssl = _rc.get("http.ssl", False)
        ssl = _truthy(_cfg_ssl) if isinstance(_cfg_ssl, str) else bool(_cfg_ssl)

    return HttpBind(host=host, port=port, ssl=ssl)
