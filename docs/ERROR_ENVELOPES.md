<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-SassyMCP-BWOBRHMUI3QF
-->
<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
-->
# Error envelopes

Tool failures reach MCP clients in four different shapes, depending on which
layer produced them. Clients should handle all four.

## 1. Plain `Error: ...` strings

Some modules return failure as a bare string, e.g. `sassymcp/modules/adb.py`
returns `f"Error: {err}"`. The client sees the raw text; there is no
machine-readable structure.

## 2. `_err()` JSON strings

`sassymcp/modules/combos.py`, `github_ops.py`, and `github_quick.py` define a
shared helper:

```python
def _err(msg: str) -> str:
    return json.dumps({"error": msg})
```

The client receives a JSON **string** containing a single `"error"` key.

## 3. `{"error": ...}` dicts

Some tools return a dict containing an `"error"` key directly, which the MCP
layer serializes, e.g. `sassymcp/_phone_status.py` returns
`{"devices": [], "adb": True, "error": f"adb error: {e}"}` and
`sassymcp/modules/app_launcher.py` returns `{"error": ..., "hint": _AX_HINT}`.

## 4. Audit-wrapper JSON (server-level)

`sassymcp/server.py` wraps sync-tool exceptions in a structured envelope:

```json
{
  "error": "<message>",
  "tool": "<tool name>",
  "retryable": true,
  "retry_hint": "<human-readable hint>",
  "retry_after_seconds": 5
}
```

The offline detector in `sassymcp/_netstate.py` can add
`"offline_alternative"` and `"hint"` keys to the same envelope.

## Deferred decision

Full normalization of these four shapes into one envelope was deemed too
invasive for this release. The accepted plan is to normalize at the wrapper
layer (shape 4) in a future release, so every failure reaches clients in the
shape-4 envelope. Until then, clients must handle all four.
