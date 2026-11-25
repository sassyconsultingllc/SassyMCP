# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-RHI2E4HO2E2M
"""Self-modification removed.

Marketplace / public builds require a fixed graded version. The former
sassy_selfmod_* tools (edit, write, hot-reload, restart, rollback) are
gone — not gated, not stubbed as callable tools. This module remains
importable so older packaging lists and smoke imports do not fail; it
never registers tools or hooks.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("sassymcp.selfmod")


def register(server) -> None:
    """No-op: self-modification tools are permanently removed."""
    logger.info("Self-modification tools removed — not registering.")
