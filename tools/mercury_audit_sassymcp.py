# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-34Y3S6NXOBFD
"""Send SassyMCP source to Inception Mercury-2 for full audit.

Mercury-2 has a 128K context window, so the SassyMCP source is split into
three chunks (core+oauth+config, modules-A, modules-B) and audited
independently. Reports are written under audits/.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://api.inceptionlabs.ai/v1/chat/completions"
MODEL = "mercury-2"
KEY = os.environ["INCEPTION_API_KEY"]
MAX_OUTPUT_TOKENS = 50000

# Resolve the SassyMCP repo root from this script's location: tools/<this>.py
# is two parents up from the package root. Override with SASSYMCP_REPO if the
# script is run from somewhere unusual.
ROOT = Path(os.environ.get("SASSYMCP_REPO") or Path(__file__).resolve().parent.parent)
PY_PKG = ROOT / "sassymcp"
PY_MODS = PY_PKG / "modules"
OAUTH_SRC = ROOT / "sassymcp-oauth" / "src"
CONFIG_FILES = [
    ROOT / "pyproject.toml",
    ROOT / "installer.wxs",
    ROOT / "sassymcp-oauth" / "wrangler.toml",
    ROOT / "sassymcp-oauth" / "package.json",
]

# README + spec docs — give Mercury-2 the project's own architectural
# context so it can reason about intent vs. implementation. Without these
# the auditor has to infer the tool surface and trust model from code
# alone, which produces shallower findings.
CONTEXT_FILES = [
    ROOT / "README.md",
    ROOT / "MIGRATION.md",
    ROOT / "docs" / "TUNNEL.md",
    ROOT / "docs" / "SassyWorks.md",
    ROOT / "docs" / "OAUTH.md",        # may not exist; filtered below
    ROOT / "docs" / "SECURITY.md",     # may not exist; filtered below
    ROOT / "docs" / "ARCHITECTURE.md", # may not exist; filtered below
]

EXCLUDE_DIR_PARTS = {"__pycache__", "_DELETE_", "node_modules", ".venv", "venv"}


def collect_py(root: Path, recursive: bool):
    out = []
    it = root.rglob("*.py") if recursive else root.glob("*.py")
    for p in it:
        if any(part in EXCLUDE_DIR_PARTS for part in p.parts):
            continue
        out.append(p)
    return sorted(out)


def collect_js(root: Path):
    out = []
    for ext in ("*.js", "*.ts", "*.mjs"):
        for p in root.rglob(ext):
            if any(part in EXCLUDE_DIR_PARTS for part in p.parts):
                continue
            out.append(p)
    return sorted(out)


def bundle(paths, label):
    parts = [f"# {label}\n"]
    for p in paths:
        try:
            rel = p.relative_to(ROOT)
        except ValueError:
            rel = p.name
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            text = f"<<read error: {e}>>"
        parts.append(f"\n\n===== FILE: {rel} =====\n{text}")
    return "".join(parts)


def split_by_size(paths, n_chunks):
    """Split paths into n roughly equal-byte chunks, preserving order."""
    sizes = [(p, p.stat().st_size) for p in paths]
    total = sum(s for _, s in sizes)
    target = total / n_chunks
    chunks = [[] for _ in range(n_chunks)]
    idx = 0
    acc = 0
    for p, s in sizes:
        chunks[idx].append(p)
        acc += s
        if acc >= target and idx < n_chunks - 1:
            idx += 1
            acc = 0
    return chunks


SYSTEM_PROMPT = """You are a principal engineer performing a rigorous, no-punches-pulled audit of a Python MCP (Model Context Protocol) server that ships to end users as a signed installer. The server exposes a large surface of tools to LLM clients (shell, file ops, registry, GitHub, ADB, Bluetooth, screen capture, UI automation, network audit, etc.) and pairs with a Cloudflare Workers OAuth bridge.

Your audit MUST cover:
1. CORRECTNESS - logic errors, off-by-one, wrong state transitions, broken invariants, dead code, unreachable branches
2. CONCURRENCY - asyncio misuse, blocking calls in async handlers, race conditions, unsafe shared mutable state, thread/loop confusion
3. SECURITY - command injection (shell=True, unsanitized argv), path traversal, SSRF, unsafe deserialization (including p1ckle / yaml.load / eval / exec), weak crypto, hardcoded secrets, leaked tokens in logs, overbroad permissions, prompt-injection-via-tool-output, TOCTOU
4. MCP TOOL SAFETY - tool surface that lets a remote LLM exfiltrate data, run arbitrary commands, escalate, or persist; missing input validation on tool args; over-privileged defaults; ability for a tool to disable safety hooks (selfmod, _security)
5. AUTH / OAUTH / LICENSE - token handling, license validation bypass, replay, weak signature checks, leaked client secrets, redirect-uri abuse, missing PKCE/state, audit-log tampering
6. RATE LIMITING / ABUSE - bypassable limits, missing limits on dangerous tools, log flooding
7. RESOURCE / LIFECYCLE - subprocess leaks, file handle leaks, never-awaited coroutines, unbounded queues, infinite recursion, unclosed sessions
8. ERROR HANDLING - swallowed exceptions, silent failures, broad except: that masks bugs, retry storms, missing backpressure
9. PACKAGING / INSTALL / UPDATER - installer running with elevated rights, update channel that fetches+executes code without signature/hash verification, writable install dir, registry/autostart abuse
10. API / IDIOMATIC - non-idiomatic Python, deprecated APIs, broken typing, asyncio anti-patterns

For every finding emit JSON objects in a single fenced ```json block, one per line, with keys:
  severity: one of [critical, high, medium, low, nit]
  category: one of the 10 above
  file: relative path
  symbol: function/class where applicable (or null)
  issue: what is wrong (one sentence)
  why: why it matters (one sentence, concrete consequence - assume an attacker controls the LLM client)
  fix: suggested fix (specific, actionable, code sketch if useful)
  confidence: 0.0-1.0 (your confidence this is a real issue, not a false positive)

After the JSON block, give a short prose "Top 5 things to fix before release" summary and an overall release-readiness verdict (ship / ship-with-fixes / hold).

Do NOT be gentle. Do NOT be generic. Every finding must be anchored to a specific file/function and a specific consequence. Skip stylistic nits unless they cause bugs. This server runs on real end-user machines with the user's privileges - assume the LLM driving it is partially adversarial. You are auditing one slice of the codebase; assume other slices exist and only flag issues you can see in this slice."""


def call_mercury(user_content, label):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
        "max_tokens": MAX_OUTPUT_TOKENS,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    print(f"[{label}] POST {API_URL}  payload={len(body)/1024:.1f} KB", flush=True)
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        print(f"[{label}] HTTP {e.code}: {err}", flush=True)
        raise
    msg = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    print(
        f"[{label}] tokens prompt={usage.get('prompt_tokens')} "
        f"completion={usage.get('completion_tokens')}",
        flush=True,
    )
    return msg


CHUNK_PROLOGUES = {
    "core": (
        "SLICE 1 of 3: SassyMCP CORE + OAUTH + PACKAGING. Includes the FastMCP server "
        "entrypoint, auth/license, the OAuth worker on Cloudflare Workers, the MSI "
        "installer manifest, and the pyproject. The full module surface (sassymcp/modules/*) "
        "is audited in slices 2 and 3. Focus on entrypoint wiring, auth/license bypasses, "
        "OAuth flow correctness, packaging/installer privilege issues."
    ),
    "modules-a": (
        "SLICE 2 of 3: SassyMCP MODULES (first half, alphabetical). These are MCP tool "
        "modules registered to the FastMCP server. Treat each @tool/@mcp.tool function as "
        "an attacker-callable endpoint. Focus on injection, path traversal, missing arg "
        "validation, privilege escalation, exfil, and concurrency."
    ),
    "modules-b": (
        "SLICE 3 of 3: SassyMCP MODULES (second half, alphabetical). Same audit lens as "
        "slice 2. Treat each @tool/@mcp.tool function as an attacker-callable endpoint."
    ),
}


def main():
    out_dir = ROOT / "audits"
    out_dir.mkdir(exist_ok=True)

    core_files = collect_py(PY_PKG, recursive=False)
    mod_files = collect_py(PY_MODS, recursive=True)
    js_files = collect_js(OAUTH_SRC) if OAUTH_SRC.exists() else []
    cfg_files = [p for p in CONFIG_FILES if p.exists()]
    ctx_files = [p for p in CONTEXT_FILES if p.exists()]

    mods_a, mods_b = split_by_size(mod_files, 2)

    chunks = {
        "core": (
            bundle(ctx_files, "PROJECT CONTEXT (README + design docs)")
            + "\n\n"
            + bundle(cfg_files, "PACKAGING / CONFIG")
            + "\n\n"
            + bundle(core_files, "PYTHON CORE (sassymcp/*.py)")
            + ("\n\n" + bundle(js_files, "OAUTH WORKER (sassymcp-oauth/src/)") if js_files else "")
        ),
        "modules-a": bundle(mods_a, "PYTHON MODULES A (sassymcp/modules/)"),
        "modules-b": bundle(mods_b, "PYTHON MODULES B (sassymcp/modules/)"),
    }

    for name, content in chunks.items():
        (out_dir / f"mercury_sassymcp_{name}_bundle.txt").write_text(content, encoding="utf-8")
        print(f"chunk {name}: {len(content)/1024:.1f} KB")

    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target == "bundle-only":
        return

    selected = [target] if target in chunks else list(chunks.keys())
    for name in selected:
        user = CHUNK_PROLOGUES[name] + "\n\n" + chunks[name]
        report = call_mercury(user, name)
        (out_dir / f"mercury_sassymcp_{name}_report.md").write_text(report, encoding="utf-8")
        print(f"wrote audits/mercury_sassymcp_{name}_report.md")


if __name__ == "__main__":
    main()
