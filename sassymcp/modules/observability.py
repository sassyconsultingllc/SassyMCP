"""SassyMCP Observability — Real-time metrics, health checks, and debug endpoints.

Exposes server metrics, health status, and tool usage stats for any MCP client
or external monitoring system.
"""

import os
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any

logger = logging.getLogger("sassymcp.observability")

# psutil is optional but expected — degrade gracefully
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


class Observability:
    def __init__(self):
        self.start_time = time.time()
        self.tool_call_count = 0
        self.error_count = 0
        self.last_error = None

    def record_call(self, success: bool = True):
        self.tool_call_count += 1
        if not success:
            self.error_count += 1

    def get_metrics(self) -> Dict[str, Any]:
        uptime = int(time.time() - self.start_time)

        metrics = {
            "uptime_seconds": uptime,
            "tool_calls_total": self.tool_call_count,
            "error_rate": round(self.error_count / max(self.tool_call_count, 1) * 100, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "1.0.0",
            "live_reload_enabled": os.environ.get("SASSYMCP_DEV") == "1",
        }

        if PSUTIL_AVAILABLE:
            metrics["cpu_percent"] = psutil.cpu_percent()
            metrics["memory_percent"] = psutil.virtual_memory().percent
            disk_root = "C:\\" if os.name == "nt" else "/"
            try:
                metrics["disk_percent"] = psutil.disk_usage(disk_root).percent
            except OSError:
                metrics["disk_percent"] = None

        return metrics

    def get_health(self) -> Dict[str, Any]:
        return {
            "status": "healthy",
            "uptime_seconds": int(time.time() - self.start_time),
            "tool_calls_total": self.tool_call_count,
            "error_count": self.error_count,
            "live_reload_enabled": os.environ.get("SASSYMCP_DEV") == "1",
        }


def register(server):
    obs = Observability()

    @server.tool()
    async def sassy_observability_metrics() -> dict:
        """Return real-time server metrics and performance data."""
        return obs.get_metrics()

    @server.tool()
    async def sassy_observability_health() -> dict:
        """Simple health check for monitoring tools and load balancers."""
        return obs.get_health()

    @server.tool()
    async def sassy_observability_tool_stats() -> dict:
        """Full usage tracker stats + pruning suggestions."""
        try:
            from sassymcp.modules._tool_loader import get_tracker
            tracker = get_tracker()
            return {
                "usage_stats": tracker.get_stats(),
                "pruning_suggestions": tracker.suggest_pruning(),
            }
        except Exception as e:
            return {"error": str(e)}

    server.observability = obs
    logger.info("Observability module loaded")
