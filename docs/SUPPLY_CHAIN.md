<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-SassyMCP-37TSVLHM3WXU
-->
<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
-->
# Supply chain

## Dependencies: pyproject ranges vs uv.lock

`pyproject.toml` declares dependency *ranges* (e.g. `mcp>=1.28.1,<2.0.0`,
`psutil>=5.9.0`, `cryptography>=50.0.0`; `pywinauto` is win32-only and the
`pyobjc-framework-*` pair is darwin-only). `uv.lock` pins exact resolved
versions within those ranges for reproducible developer builds (`uv sync`).
The two have been verified consistent; `uv.lock` is only consumed by `uv`
— **pip installs do not read `uv.lock`** and resolve from the pyproject
ranges at install time.

## SHA-256 sidecar verification and its trust limit

The self-updater (`sassymcp/modules/updater.py`) verifies downloaded release
assets against a SHA-256 sidecar: `_fetch_checksum()` looks for
`<asset_name>.sha256`, `SHA256SUMS`, or `checksums.txt` in the release's
asset list, and `apply(..., require_checksum=True)` refuses to hand back a
run command when a sidecar is published but verification fails. When no
sidecar is published (many older releases predate sidecar publication), the
check downgrades to a warning rather than refusing.

Trust limit: the sidecar is fetched from the same channel (the GitHub release
page) as the asset it verifies, so a compromised channel could replace both.
This is an accepted residual — real offline signing (e.g. sigstore) is a
future step.

## Publishing path

PyPI publishes use OIDC Trusted Publishing (`pypa/gh-action-pypi-publish`
with `id-token: write`); no long-lived PyPI API token exists in CI. The
Windows exe, macOS universal binary, DXT/MCPB bundle, and VSIX are built on
GitHub-hosted runners per `.github/workflows/release.yml`.
