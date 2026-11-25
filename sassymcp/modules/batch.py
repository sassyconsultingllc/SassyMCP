# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-modules-YNTHJO5CODHB
"""SassyMCP Batch — fan out many tool calls from a single request.

Why this exists. SassyMCP already runs *concurrent client calls* concurrently:
the audit wrapper offloads sync tool bodies with ``asyncio.to_thread`` and
``_wrap_all_tools`` forces ``is_async=True``, so in-flight calls from different
sessions interleave instead of wedging each other. What there was no way to
express is **intra-call fan-out** — "run these nineteen probes and hand me the
results" — because every operation needed its own client round trip, each paying
full protocol overhead and each hitting the per-group rate limiter separately.

``sassy_batch`` takes a list of ``{tool, args}`` operations, runs them
concurrently under a semaphore, and returns one structured result per operation.

**This is a scheduling primitive, not a policy bypass.** Every operation is
dispatched through ``ToolManager.call_tool``, which is the same path a client
call takes, so each one still passes argument validation, the audit wrapper, the
security/confirmation layer and the per-group rate limiter. Batching changes when
calls happen, never whether they are allowed.

A failing operation is recorded and the rest continue — one bad tool name does
not sink the batch. Set ``stop_on_error=True`` for pipelines where a later step
is meaningless once an earlier one fails.

Load-bearing dependency
-----------------------
Both the concurrency and the per-operation timeout depend on
``server._wrap_all_tools()`` having run. It replaces each sync tool body with a
coroutine that offloads to ``asyncio.to_thread`` and forces ``is_async=True``.
Without it FastMCP invokes a ``def``-declared tool **inline on the event loop**,
which means ``asyncio.gather`` cannot interleave and ``asyncio.wait_for`` never
gets a chance to cancel.

This is not theoretical — measured against an unwrapped harness, six 0.4s
operations at ``max_concurrent=6`` took 2.40s (fully serial) and a 0.3s timeout
against a 2s tool never fired. With production wrapping the same batch took
0.41s and the timeout fired correctly. If batching ever looks serial, check that
the tools were wrapped rather than tuning ``max_concurrent``.
"""

import asyncio
import json
import logging
import time
from typing import Any, TypedDict

logger = logging.getLogger("sassymcp.batch")

# Hard ceiling on fan-out width. Guards against a malformed request turning into
# a self-inflicted denial of service against the machine or an upstream API.
MAX_OPERATIONS = 50
MAX_CONCURRENT = 16

# Tools that must never appear inside a batch. Recursion here would let one
# request expand exponentially.
DENIED_TOOLS = {"sassy_batch"}


class BatchOpResult(TypedDict):
    """One operation's outcome. `result` and `error` are always present; the
    unused one is None, which keeps the output schema stable."""

    index: int
    tool: str
    ok: bool
    elapsed_ms: int
    result: Any
    error: str | None


class BatchResult(TypedDict):
    requested: int
    succeeded: int
    failed: int
    elapsed_ms: int
    max_concurrent: int
    results: list[BatchOpResult]


def _coerce(value: Any) -> Any:
    """Unwrap a tool result that is a JSON string into a real object.

    Most SassyMCP tools are still annotated ``-> str`` and return
    ``json.dumps(...)``, which reaches a caller double-encoded. Parsing it here
    means a batch consumer gets objects today without waiting for all ~270 tools
    to be converted. Anything that is not JSON is passed through untouched.
    """
    if isinstance(value, str):
        s = value.lstrip()
        if s[:1] in ("{", "["):
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                return value
    return value


def _parse_operations(operations: str) -> list[dict]:
    """Parse and validate the operations payload, raising ValueError with a
    message aimed at the caller rather than a stack trace."""
    try:
        parsed = json.loads(operations)
    except (ValueError, TypeError) as e:
        raise ValueError(f"operations is not valid JSON: {e}") from None

    # Wrong *type* raises TypeError, wrong *value* raises ValueError. The caller
    # catches both and turns either into a structured result, so the distinction
    # costs nothing at the boundary and keeps the helper honest if reused.
    if not isinstance(parsed, list):
        raise TypeError("operations must be a JSON array of {\"tool\": ..., \"args\": {...}}")
    if not parsed:
        raise ValueError("operations is empty")
    if len(parsed) > MAX_OPERATIONS:
        raise ValueError(f"{len(parsed)} operations requested; the ceiling is {MAX_OPERATIONS}")

    cleaned = []
    for i, op in enumerate(parsed):
        if not isinstance(op, dict):
            raise TypeError(f"operation {i} is {type(op).__name__}, expected an object")
        tool = op.get("tool")
        if not tool or not isinstance(tool, str):
            raise ValueError(f"operation {i} is missing a string 'tool'")
        args = op.get("args", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise TypeError(f"operation {i} has 'args' of type {type(args).__name__}, expected an object")
        cleaned.append({"tool": tool, "args": args})
    return cleaned


def register(server):
    """Register the batch fan-out tool."""

    @server.tool()
    async def sassy_batch(
        operations: str,
        max_concurrent: int = 5,
        timeout_seconds: float = 60.0,
        stop_on_error: bool = False,
    ) -> BatchResult:
        """Run many SassyMCP tools concurrently in one call.

        operations: JSON array of {"tool": "<name>", "args": {...}}, e.g.
            [{"tool":"sassy_http","args":{"url":"https://a"}},
             {"tool":"sassy_http","args":{"url":"https://b"}}]
        max_concurrent: how many run at once (1-16, default 5). Lower this when
            the targets share a rate limit or a thin upstream quota.
        timeout_seconds: per-operation ceiling, not a total for the batch.
        stop_on_error: cancel operations that have not started once one fails.

        Returns one structured result per operation, in request order, each with
        ok / elapsed_ms / result / error. A failed operation never raises — read
        its `ok` field. Every call still goes through the normal validation,
        audit and rate-limit path; batching is scheduling, not an escape hatch.
        """
        started = time.monotonic()

        try:
            ops = _parse_operations(operations)
        except (TypeError, ValueError) as e:
            return BatchResult(
                requested=0, succeeded=0, failed=0,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                max_concurrent=0,
                results=[BatchOpResult(index=-1, tool="", ok=False, elapsed_ms=0,
                                       result=None, error=str(e))],
            )

        width = max(1, min(int(max_concurrent), MAX_CONCURRENT))
        sem = asyncio.Semaphore(width)
        manager = server._tool_manager
        abort = asyncio.Event()

        async def run_one(index: int, op: dict) -> BatchOpResult:
            tool_name = op["tool"]
            op_started = time.monotonic()

            def done(ok: bool, result: Any = None, error: str | None = None) -> BatchOpResult:
                return BatchOpResult(
                    index=index, tool=tool_name, ok=ok,
                    elapsed_ms=int((time.monotonic() - op_started) * 1000),
                    result=result, error=error,
                )

            if tool_name in DENIED_TOOLS:
                return done(False, error=f"'{tool_name}' cannot be nested inside a batch")

            if manager.get_tool(tool_name) is None:
                return done(False, error=f"unknown tool '{tool_name}'")

            if stop_on_error and abort.is_set():
                return done(False, error="skipped: an earlier operation failed and stop_on_error is set")

            async with sem:
                # Re-check after acquiring; the abort may have been set while queued.
                if stop_on_error and abort.is_set():
                    return done(False, error="skipped: an earlier operation failed and stop_on_error is set")
                try:
                    raw = await asyncio.wait_for(
                        manager.call_tool(tool_name, op["args"], context=None, convert_result=False),
                        timeout=timeout_seconds,
                    )
                    return done(True, result=_coerce(raw))
                except TimeoutError:
                    if stop_on_error:
                        abort.set()
                    return done(False, error=f"timed out after {timeout_seconds}s")
                except Exception as e:
                    if stop_on_error:
                        abort.set()
                    return done(False, error=f"{type(e).__name__}: {e}")

        settled = await asyncio.gather(
            *(run_one(i, op) for i, op in enumerate(ops)),
            return_exceptions=True,
        )

        results: list[BatchOpResult] = []
        for i, r in enumerate(settled):
            if isinstance(r, BaseException):
                # gather itself should not surface exceptions - run_one catches - but
                # never let a batch disappear into a traceback.
                results.append(BatchOpResult(
                    index=i, tool=ops[i]["tool"], ok=False, elapsed_ms=0,
                    result=None, error=f"{type(r).__name__}: {r}",
                ))
            else:
                results.append(r)

        succeeded = sum(1 for r in results if r["ok"])
        logger.info("batch: %d ops, %d ok, %d failed, width=%d",
                    len(results), succeeded, len(results) - succeeded, width)

        return BatchResult(
            requested=len(ops),
            succeeded=succeeded,
            failed=len(results) - succeeded,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            max_concurrent=width,
            results=results,
        )

    logger.info("Batch fan-out loaded")
