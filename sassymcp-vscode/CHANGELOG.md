<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-SassyMCP-EQEZTC7TCMRF
-->
# Changelog

*Last updated: 2026-05-20*

## 1.5.0 — 2026-05-20

MadameClaude memory + frozen-exe completeness:
- PyInstaller spec now lists `combos`, `prompts`, and `_confirm` as hidden imports. Previous frozen exes silently dropped the combo tools (`sassy_combo_pr_review`, `sassy_combo_phone_observe`, `sassy_combo_codebase_grep`) and all six MCP slash-menu prompts (`pr-review`, `phone-status`, `resume`, `codebase-grep`, `brain-status`, `setup-sassy`). Re-run via `sassymcp.exe --help` shows them registered now.
- Default DNS-rebinding allowlist is now loopback-only (`localhost,127.0.0.1`). To expose the server through a Cloudflare Tunnel or LAN, set `SASSYMCP_ALLOWED_HOSTS=mcp.<your-domain>.tld,localhost,127.0.0.1`. The shipped launchers (`start-lan.bat`, `start-tunnel.bat`) carry no vendor-specific hostname — see `docs/TUNNEL.md` for a step-by-step Cloudflare Tunnel walkthrough.
- `sassymcp-oauth/wrangler.toml` is now an `.example` template; deployers copy it to `wrangler.toml` and substitute their own hostname + KV namespace id. The working file is gitignored so production identifiers no longer leak into forks.
- DXT, VSIX, and EXE all version-stamped to 1.5.0 from the canonical `sassymcp/__init__.py`. CI release pipeline (`.github/workflows/release.yml`) refuses to build if the git tag and `__version__` disagree.

## 1.4.2 — 2026-05-15

Bugfix:
- `sassy_shell` now auto-promotes any call with `timeout_seconds > 120` to a background session and returns a poll-able session handle instead of blocking. Synchronous waits past the MCP client's ~240s response wall were wedging the JSON-RPC connection, leaving zombie subprocesses and blocking every subsequent tool call until the client was restarted.

## 1.4.1 — 2026-05-05

Marketplace-readiness fixes:
- Bundle the MIT LICENSE file inside the extension (was at repo root only — vsce required it inside `sassymcp-vscode/` for marketplace publish)
- CI workflow opts into Node.js 24 for all JavaScript actions (silences the GitHub deprecation warnings; node20 sunset is Sept 2026)

## 1.4.0 — 2026-05-04

Multi-client integration release.

- Auto-deploys cross-client tool playbook (Claude Skill / Cursor rules / Windsurf memories / Cline rules) when activated alongside the install CLI
- Pulls in the new SassyMCP combo tools (`sassy_combo_pr_review`, `sassy_combo_phone_observe`, `sassy_combo_codebase_grep`)
- MCP prompts surface as slash-menu shortcuts in Copilot agent mode (`pr-review`, `phone-status`, `resume`, `codebase-grep`, `brain-status`, `setup-sassy`)
- Status bar respects new license-tier signals from the server's usage-score-boosted loader

## 1.3.5 — 2026-05-03

Initial release.

- Auto-detects sassymcp.exe on PATH (or honors `sassymcp.exePath` setting)
- On activation, runs `sassymcp install` to patch every detected MCP client config
- Status bar shows license tier and brain health
- Commands: Run Setup Wizard, Reinstall Configs, Open Audit Log, Open _DELETE_ Folder, Show Brain Status
- Setup Wizard webview for first-time persona configuration
