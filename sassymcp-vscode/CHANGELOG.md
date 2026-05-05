# Changelog

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
