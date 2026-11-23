# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
"""`sassymcp tunnel` — one-command Cloudflare quick tunnel with auto allow-list.

The operator runs a single command and the tunnel just works:

    sassymcp tunnel [--host 127.0.0.1] [--port 21001]

What it does:
  1. Warns (but continues) if nothing is listening on the local
     bridge address yet — the tunnel would 502 until the bridge starts.
  2. Starts ``cloudflared tunnel --url http://<host>:<port>`` in the
     foreground and streams its log through to the terminal.
  3. Reads the minted ``*.trycloudflare.com`` hostname out of
     cloudflared's output and allow-lists it via the same file mechanism
     as ``sassymcp allow-host`` (``~/.sassymcp/allowed_hosts``), so a
     running server accepts it on its next request with no restart and
     no manual step. If the server is down, the file seeds its next boot.
  4. Keeps running (like ``start-tunnel.bat``) until Ctrl-C; stopping it
     stops the tunnel.

The tunnel hostname is parsed, never guessed: cloudflared prints a line
of the form ``Your quick Tunnel has been created! Visit it at
(https://<name>.trycloudflare.com)``. If cloudflared changes its log
format, the worst case is a clear "could not determine the tunnel
hostname" message and a prompt to run ``sassymcp allow-host`` by hand —
the tunnel itself keeps running either way.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys

# Matches the hostname in cloudflared's "Your quick Tunnel has been
# created! Visit it at (https://<name>.trycloudflare.com)" line (and any
# other line that happens to carry a trycloudflare URL).
_TUNNEL_URL_RE = re.compile(
    r"https?://([A-Za-z0-9][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)*\.trycloudflare\.com)",
    re.IGNORECASE,
)


class TunnelError(Exception):
    """Raised when the tunnel cannot be started (e.g. cloudflared missing)."""


def extract_tunnel_host(line: str) -> str | None:
    """Return the trycloudflare hostname in a cloudflared log line, if any."""
    m = _TUNNEL_URL_RE.search(line or "")
    return m.group(1).lower() if m else None


def _port_listening(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def run_quick_tunnel(
    *,
    host: str = "127.0.0.1",
    port: int = 21001,
    cloudflared: str = "cloudflared",
    on_host=None,
    out=None,
    warn_if_port_closed: bool = True,
) -> int:
    """Start a Cloudflare quick tunnel, auto allow-list its hostname.

    Blocks until cloudflared exits (or Ctrl-C). Returns cloudflared's
    exit code (130 on Ctrl-C). Raises TunnelError if cloudflared cannot
    be started at all. ``on_host`` is called once with the minted
    hostname, after it has been allow-listed.
    """
    from sassymcp._allowed_hosts import add_host

    out = out if out is not None else sys.stdout

    def _emit(msg: str) -> None:
        print(msg, file=out, flush=True)

    if warn_if_port_closed and not _port_listening(host, port):
        _emit(
            f"WARNING: nothing is listening on {host}:{port} — the tunnel "
            "will answer 502 until the bridge starts. Start it with "
            f"`sassymcp --http --host {host} --port {port}` (or let "
            "start-tunnel.bat do it) and the tunnel will pick it up."
        )

    cmd = [cloudflared, "--no-autoupdate", "tunnel", "--url", f"http://{host}:{port}"]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as e:
        raise TunnelError(
            f"cloudflared not found ({cloudflared!r}). Install it first — "
            "Windows: `winget install Cloudflare.cloudflared`; macOS: "
            "`brew install cloudflare/cloudflare/cloudflared`; Linux: see "
            "https://developers.cloudflare.com/cloudflare-one/connections/"
            "connect/downloads/ — then retry."
        ) from e
    except OSError as e:
        raise TunnelError(f"could not start cloudflared ({e})") from e

    announced: str | None = None
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            _emit(line.rstrip("\n"))
            if announced is None:
                found = extract_tunnel_host(line)
                if found:
                    try:
                        add_host(found)
                    except ValueError as e:
                        _emit(
                            f"WARNING: could not allow-list {found} ({e}) — "
                            "run `sassymcp allow-host " + found + "` by hand."
                        )
                        announced = found  # don't retry every line
                        continue
                    announced = found
                    _emit(
                        f"SassyMCP: tunnel live at https://{found}/mcp "
                        f"— host {found} allow-listed (no restart needed)."
                    )
                    if on_host is not None:
                        try:
                            on_host(found)
                        except Exception:
                            pass
        return proc.wait()
    except KeyboardInterrupt:
        _emit("Stopping tunnel (Ctrl-C)…")
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            return proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            return 130
    finally:
        if announced is None:
            _emit(
                "NOTE: no trycloudflare hostname was seen in cloudflared's "
                "output — if the tunnel did start, allow-list it by hand: "
                "`sassymcp allow-host <hostname>`."
            )
