# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-AWDBUXO5EFNQ
# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
"""Value-level secret masking tests for sassymcp.modules.audit (2026-09-21).

Covers the conservative extension of _VALUE_SECRET_PATTERNS (xoxp-,
xoxe- Slack tokens, AIza Google API keys) and pins the no-false-positive
behavior on ordinary prose.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from sassymcp.modules.audit import _redact_value

REDACTED_CASES = [
    # (input fragment, expected: fully redacted substring absent)
    "deploy token xoxp-EXAMPLE-NOT-A-REAL-TOKEN-000000",
    "err: unauthorized xoxe-1AbCdEfGhIj-1234567890abcdef using bad auth",
    "key=AIzaSyDaGmWKa4JsGhn9example35charsXYZ12 failed",
    "ghp_abcdefghijklmnopqrstuvwxyz123456 leaked in argv",
    "xoxb-EXAMPLE-NOT-A-REAL-TOKEN-111111 still covered",
]

PROSE_CASES = [
    "plain hello world",
    "the xoxo festival was great",          # not a token prefix
    "AIza is not a key by itself",          # prefix without the body
    "xoxp- too short",                      # prefix without the body
]


@pytest.mark.parametrize("value", REDACTED_CASES)
def test_known_token_shapes_are_redacted(value):
    out = _redact_value(value)
    assert "***REDACTED***" in out
    # the distinctive prefix must not survive redaction
    for prefix in ("xoxp-", "xoxe-", "xoxb-", "AIza", "ghp_"):
        if prefix in value:
            assert prefix not in out, f"prefix leaked: {out!r}"


@pytest.mark.parametrize("value", PROSE_CASES)
def test_ordinary_prose_is_not_redacted(value):
    assert _redact_value(value) == value


def test_redaction_preserves_surrounding_text():
    out = _redact_value("before xoxp-EXAMPLE-NOT-A-REAL-TOKEN-000000 after")
    assert out.startswith("before ***REDACTED***")
    assert out.endswith("after")
