<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-Projects-WHFLELVYXV4L
-->
## v1.14.3 — Agent-guidance accuracy fixes

Every tool name the shipped guidance hands to an AI client now resolves to a real tool. Previously six names did not exist, so a model following the playbook would emit failing tool calls.

### Fixed

- Six non-existent tool names corrected across the shipped skill file, the setup-wizard-generated persona doc, and two runtime hook playbooks:
  `sassy_github_quick_pr` → `sassy_combo_pr_review`, `sassy_arp` → `sassy_arp_table`, `sassy_reg_autoruns` → `sassy_autorun_entries`, `sassy_wifi_scan` → `sassy_wifi_networks`, `sassy_security_audit_certs` → `sassy_cert_check`, `sassy_url_security_headers` → `sassy_url_headers`.
- PR-review guidance now points at `sassy_combo_pr_review` (not `sassy_ghq_pr`, which creates PRs).
- Two false capability claims removed (`sassy_linux_exec` has no `allow_destructive`; `sassy_autorun_entries` covers Run/RunOnce only).
- Phantom `madame_*` aliases no longer advertised.

### Changed

- Personal/internal references scrubbed from user-facing surfaces.

### SHA256

```
sassymcp-v1.14.3.mcpb     0a4a37585bfc80043a91146b77671cbfe048de1929719cffb9818dc46a0706ed
sassymcp.exe              c6cad77abefb65b8647df213e82f9befbf86a1217677e9945556365302fd51b4
sassymcp-macos-arm64      22dde6c32b898cac1bb47ad99553e397096dbe0021de140c6fab7015228a37c7
```

PyPI: https://pypi.org/project/sassymcp/1.14.3/

Note: macOS artifact is Apple Silicon (arm64) only. The universal2 merge job in CI did not finish (Intel runner queue), so this release ships the native arm64 binary that did build.
