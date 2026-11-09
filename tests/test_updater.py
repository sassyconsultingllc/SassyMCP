# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-QBQ3TPXGS7R6
"""Updater module unit tests — version comparator and offline state shape.

Live GitHub calls are not made here; the smoke test in development verifies
the network path manually.
"""

from sassymcp.modules.updater import Updater, _normalize


def test_version_compare_basic_ordering():
    assert _normalize("1.0.0") < _normalize("1.0.1")
    assert _normalize("1.0.0") < _normalize("1.1.0")
    assert _normalize("1.0.0") < _normalize("2.0.0")


def test_prerelease_sorts_below_release():
    assert _normalize("1.3.0-dev") < _normalize("1.3.0")
    assert _normalize("1.3.0-rc1") < _normalize("1.3.0")
    assert _normalize("1.2.0") < _normalize("1.3.0-dev")


def test_v_prefix_normalized():
    assert _normalize("v1.2.0") == _normalize("1.2.0")
    assert _normalize("V1.2.0") == _normalize("1.2.0")


def test_check_handles_api_error_offline(monkeypatch):
    upd = Updater()

    def boom(self, url, timeout=10.0):
        import urllib.error
        raise urllib.error.URLError("simulated offline")

    monkeypatch.setattr(Updater, "_http_json", boom)
    result = upd.check(force=True)
    assert "error" in result
    assert "offline" in result["error"].lower() or "unreachable" in result["error"].lower()


def test_list_assets_propagates_error_when_unreachable(monkeypatch):
    upd = Updater()

    def boom(self, url, timeout=10.0):
        import urllib.error
        raise urllib.error.URLError("nope")

    monkeypatch.setattr(Updater, "_http_json", boom)
    result = upd.list_assets()
    assert "error" in result


class _FakeResp:
    def __init__(self, payload=b"payload"):
        self._payload = payload
        self._done = False

    def read(self, _n=-1):
        if self._done:
            return b""
        self._done = True
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def test_apply_refuses_without_sidecar(tmp_path, monkeypatch):
    """Default require_checksum=True is fail-closed when no sidecar exists."""
    import urllib.request

    upd = Updater()
    monkeypatch.setattr(
        upd,
        "list_assets",
        lambda tag=None: {
            "tag": "v1.16.0",
            "assets": [{
                "name": "sassymcp.exe",
                "download_url": "https://example.invalid/sassymcp.exe",
            }],
        },
    )
    monkeypatch.setattr(upd, "_fetch_checksum", lambda tag, name: (None, None))
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: _FakeResp()
    )
    dest = tmp_path / "updates"
    result = upd.apply("sassymcp.exe", tag="v1.16.0", dest_dir=str(dest))
    assert "error" in result
    assert "sidecar" in result["error"].lower()
    staged = dest / "sassymcp.exe"
    assert not staged.exists()


def test_apply_allows_legacy_without_sidecar_when_opted_out(tmp_path, monkeypatch):
    import urllib.request

    upd = Updater()
    monkeypatch.setattr(
        upd,
        "list_assets",
        lambda tag=None: {
            "tag": "v1.14.0",
            "assets": [{
                "name": "sassymcp.exe",
                "download_url": "https://example.invalid/sassymcp.exe",
            }],
        },
    )
    monkeypatch.setattr(upd, "_fetch_checksum", lambda tag, name: (None, None))
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: _FakeResp()
    )
    dest = tmp_path / "updates"
    result = upd.apply(
        "sassymcp.exe", tag="v1.14.0", dest_dir=str(dest), require_checksum=False
    )
    assert "error" not in result
    assert result.get("checksum", {}).get("status") == "no_sidecar_published"
    assert (dest / "sassymcp.exe").exists()
