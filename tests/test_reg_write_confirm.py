# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-REGCONFIRM01
"""sassy_reg_write must refuse without confirm='YES' even on Windows."""
import inspect

from sassymcp.modules import registry as registry_mod


def test_reg_write_signature_requires_confirm():
    src = inspect.getsource(registry_mod.register)
    assert "confirm: str" in src
    assert "confirm != \"YES\"" in src or "confirm != 'YES'" in src
