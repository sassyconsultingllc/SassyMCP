# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
"""Tests for `sassymcp tunnel` (sassymcp/_tunnel.py).

The hostname parser is tested against real cloudflared log lines; the
runner is tested with a fake `cloudflared` script so no network or
real tunnel is needed.
"""
from __future__ import annotations

import io
import os
import stat

import pytest

from sassymcp import _allowed_hosts as ah
from sassymcp._tunnel import TunnelError, extract_tunnel_host, run_quick_tunnel


@pytest.fixture
def isolated_hosts(tmp_path, monkeypatch):
    # NOTE: resolve _paths lazily, NOT from the module-level import.
    # test_lemonsqueezy / test_license_gating delete sys.modules["sassymcp._paths"]
    # and re-import it under a fresh SASSYMCP_HOME, so the object bound at
    # import time can be stale; the live sys.modules entry is the only
    # correct patch target (see _allowed_hosts._hosts_file()).
    import sassymcp._paths as live_paths

    fake = tmp_path / "allowed_hosts"
    monkeypatch.setattr(live_paths, "ALLOWED_HOSTS_FILE", fake)
    monkeypatch.delenv("SASSYMCP_ALLOWED_HOSTS", raising=False)
    ah._cache_key = None
    ah._cached = []
    ah._provisioned.clear()
    yield fake
    ah._cache_key = None
    ah._cached = []
    ah._provisioned.clear()


# ── hostname extraction ───────────────────────────────────────────────

@pytest.mark.parametrize("line,expected", [
    ("2026-11-22T23:00:00Z INF |  Your quick Tunnel has been created! "
     "Visit it at (https://abc-123-xyz.trycloudflare.com)",
     "abc-123-xyz.trycloudflare.com"),
    ("2026-11-22T23:00:00Z INF Registered tunnel connection "
     "https://weed-newly-retail-sci.trycloudflare.com",
     "weed-newly-retail-sci.trycloudflare.com"),
    ("INF |  https://UPPER-CASE.trycloudflare.com/mcp",
     "upper-case.trycloudflare.com"),
    ("INF |  tunnel started, no URL here yet", None),
    ("INF |  https://example.com is not a tunnel host", None),
    ("", None),
    (None, None),
])
def test_extract_tunnel_host(line, expected):
    assert extract_tunnel_host(line) == expected


# ── runner with a fake cloudflared ────────────────────────────────────

def _fake_cloudflared(tmp_path, *, exit_code=0, sleep="0.2"):
    """A stand-in cloudflared that prints a tunnel URL then exits."""
    script = tmp_path / "cloudflared"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        "print('2026-11-22T23:00:00Z INF Starting tunnel', flush=True)\n"
        "print('2026-11-22T23:00:00Z INF |  Your quick Tunnel has been created! '\n"
        "      'Visit it at (https://fake-999.trycloudflare.com)', flush=True)\n"
        f"time.sleep({sleep})\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    os.chmod(script, stat.S_IRWXU)
    return str(script)


def test_run_quick_tunnel_allowlists_host(tmp_path, isolated_hosts):
    out = io.StringIO()
    seen: list[str] = []
    rc = run_quick_tunnel(
        host="127.0.0.1",
        port=21001,
        cloudflared=_fake_cloudflared(tmp_path),
        on_host=seen.append,
        out=out,
        warn_if_port_closed=False,
    )
    assert rc == 0
    assert seen == ["fake-999.trycloudflare.com"]
    assert "fake-999.trycloudflare.com" in ah.get_file_hosts()
    assert "tunnel live at https://fake-999.trycloudflare.com/mcp" in out.getvalue()


def test_run_quick_tunnel_warns_when_port_closed(tmp_path, isolated_hosts):
    out = io.StringIO()
    rc = run_quick_tunnel(
        host="127.0.0.1",
        port=21999,  # nothing listens here in the test env
        cloudflared=_fake_cloudflared(tmp_path),
        out=out,
        warn_if_port_closed=True,
    )
    assert rc == 0
    assert "nothing is listening on 127.0.0.1:21999" in out.getvalue()
    # The host is still allow-listed even though the bridge was down.
    assert "fake-999.trycloudflare.com" in ah.get_file_hosts()


def test_run_quick_tunnel_missing_binary(isolated_hosts):
    with pytest.raises(TunnelError, match="cloudflared not found"):
        run_quick_tunnel(
            cloudflared="/nonexistent/cloudflared",
            out=io.StringIO(),
            warn_if_port_closed=False,
        )


def test_run_quick_tunnel_no_hostname_note(tmp_path, isolated_hosts):
    script = tmp_path / "cloudflared"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "print('INF starting, but no URL line', flush=True)\n",
        encoding="utf-8",
    )
    os.chmod(script, stat.S_IRWXU)
    out = io.StringIO()
    rc = run_quick_tunnel(
        cloudflared=str(script), out=out, warn_if_port_closed=False,
    )
    assert rc == 0
    assert "no trycloudflare hostname was seen" in out.getvalue()
    assert ah.get_file_hosts() == []
