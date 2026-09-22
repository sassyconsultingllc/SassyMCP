<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-SassyMCP-LONKGQFEOL3Z
-->
# Release provenance — SassyMCP v1.16.0

How a SassyMCP release is built, published, and verified end-to-end: from
the git tag, through the GitHub Actions release workflow, through OIDC
Trusted Publishing to PyPI and the MCP Registry, to what a user can
actually verify on their machine.

## Provenance chain (verified from `.github/workflows/release.yml`)

Single workflow file: `.github/workflows/release.yml` (`name: Release`).

1. **Trigger — the tag push.** `on: push: tags: - "v*.*.*"` (plus
   `workflow_dispatch` for manual artifact builds, which never publish).
   The owner pushes a tag like `v1.16.0` after commit. Every build job
   first asserts the tag matches `__version__` in `sassymcp/__init__.py`
   and fails the run otherwise — you cannot tag `v1.16.0` while the
   source still says `1.15.2`.
2. **Build jobs (per-platform, GitHub-hosted runners).**
   - `build-exe` — PyInstaller builds `dist/sassymcp.exe` on
     `windows-latest`; smoke-tested via `--help`.
   - `build-dxt` — packs the exe into a byte-identical
     `sassymcp-v<ver>.mcpb` + `sassymcp-v<ver>.dxt` bundle with the
     official `@anthropic-ai/mcpb` CLI. Manifest version auto-synced to
     `__version__`.
   - `build-vsix` — `vsce` packages the VS Code extension; `package.json`
     version synced to `__version__` first.
   - `build-macos` (x86_64 + arm64 native runners) →
     `build-macos-universal` — `lipo` merges a universal2
     `sassymcp-macos` binary. **Note: unsigned** — Gatekeeper quarantines
     it as an unidentified developer binary (see `publish` step in the
     workflow for the exact user workaround).
3. **GitHub Release** (`release` job). Downloads all artifacts and creates
   the GitHub Release with `gh release create`, attaching `sassymcp.exe`,
   `sassymcp-macos`, the `.dxt`/`.mcpb`, and the `.vsix`. Release notes
   come from `docs/releases/v<ver>.md` when present, else auto-generated.
4. **PyPI** (`publish-pypi` job). Builds sdist + wheel fresh in CI
   (`python -m build`), verifies the `mcp-name` ownership marker survives
   into wheel METADATA (the MCP Registry later checks it), then publishes
   with `pypa/gh-action-pypi-publish@release/v1` using
   `permissions: id-token: write` — **OIDC Trusted Publishing. No PyPI
   API token exists in CI; nothing to leak or rotate.** `skip-existing:
   true` so a re-run after a successful PyPI upload doesn't hard-fail
   (the v1.15.0 lesson) and doesn't block the registry publish.
5. **MCP Registry** (`publish-registry` job, `needs: [release, publish-pypi]`).
   Stamps `server.json` with this release's version, the `.mcpb` release-asset
   URL, and its SHA-256 (`fileSha256`); confirms the asset URL returns 200;
   authenticates with `./mcp-publisher login github-oidc` (again OIDC, no
   secret); then `validate` + `publish`.
6. **VS Code Marketplace** (`publish-vsix` job). Only fires on real tags
   **and** when the repo variable `PUBLISH_VSIX == 'true'`, using the
   `VSCE_PAT` secret — currently a token-based path, not OIDC.

## What is attested today

- **OIDC Trusted Publishing for PyPI and the MCP Registry** — the CI job,
  not a human, presents a short-lived identity to PyPI/`mcp-publisher`;
  there are no long-lived publish tokens for those two surfaces.
- **Tag↔source version lock** — three jobs independently fail if
  `refs/tags/vX.Y.Z` ≠ `sassymcp/__init__.py` `__version__`.
- **Reproducible-ish builds in CI** — every artifact is rebuilt from the
  tagged commit on fresh hosted runners; the VSIX/DXT/manifest versions
  are mechanically synced to `__version__`, not hand-edited.
- **SHA-256 sidecar naming the self-updater understands** —
  `sassymcp/modules/updater.py::_fetch_checksum()` looks for
  `<asset_name>.sha256`, `SHA256SUMS`, or `checksums.txt` in the release
  asset list and refuses to complete an update when a published sidecar
  fails verification (`require_checksum=True`).

## What is NOT attested today (follow-ups, not blockers)

- **No SLSA / Sigstore attestations.** Neither PyPI uploads nor GitHub
  release assets carry signed build provenance. Adding this would take:
  adding `attestations: write` to the relevant job permissions and a
  step running `actions/attest-build-provenance@v2` (or
  `pypa/gh-action-pypi-publish`'s built-in attestations, which need no
  workflow change beyond trusting the action version), then publishing the
  resulting `.sigstore.json` / `.attestation` bundles alongside the
  release assets and documenting `sigstore verify` for users.
- **The self-updater's SHA-256 check has a trust limit** (see
  `docs/SUPPLY_CHAIN.md`): the sidecar travels in the same channel as the
  asset, so it detects corruption/mismatch but cannot prove origin by
  itself.
- **macOS universal binary is unsigned** — no Developer ID signing or
  notarization; Gatekeeper flags it until the user clears quarantine.
  Signing would need an Apple Developer account + cert/notary secrets
  wired into the `build-macos-universal` job.
- **VSIX publish still uses a long-lived `VSCE_PAT` token** rather than
  OIDC.
- **A SHA256SUMS file is not currently attached to GitHub releases** —
  the updater supports it, but nothing in `release.yml` generates or
  uploads one, so the check often degrades to a warning. (The
  `SHA256SUMS` next to the local wheel/sdist in this task's `dist/` is a
  manual step, not part of CI.)

## The dist artifacts and their SHA256SUMS (v1.16.0)

Generated in CI by `python -m build` (`publish-pypi` job); rebuilt locally
2026-09-21 for release staging. Filename pattern:

- `sassymcp-1.16.0-py3-none-any.whl`
- `sassymcp-1.16.0.tar.gz`

Checksums (file `SHA256SUMS`, GNU coreutils format):

```
be1d9bae1680e61d3cf792714313e6213d4a0cc2c65db9ebcb132ca5a5adfe97  sassymcp-1.16.0-py3-none-any.whl
26e18a4955802de8da5e7252c2cbeba729e297854d18afe67b2d019a077aba0f  sassymcp-1.16.0.tar.gz
```

Both artifacts' internal metadata (`METADATA` in the wheel, `PKG-INFO` in
the sdist) were sanity-checked to carry `Name: sassymcp`, `Version:
1.16.0`.

## How a user verifies a download

**Manual check (any artifact):**

```sh
sha256sum -c SHA256SUMS        # in the same directory as the artifacts
# or, without the file:
sha256sum sassymcp-1.16.0-py3-none-any.whl
# compare the output with the SHA256SUMS entry above
```

**At install time (pip hash-checking mode):**

```sh
pip download sassymcp==1.16.0 --no-deps -d ./pkgs
pip install --require-hashes -r <(printf 'sassymcp==1.16.0 \\\n    --hash=sha256:be1d9bae1680e61d3cf792714313e6213d4a0cc2c65db9ebcb132ca5a5adfe97\n') ./pkgs/sassymcp-1.16.0-py3-none-any.whl
```

Note the integrity caveat: hashes here prove *file integrity*, not origin.
Until Sigstore/SLSA attestations are added (see above), origin rests on
trust in the GitHub release channel and the OIDC-published PyPI listing —
PyPI itself records the Trusted Publisher identity on the release page,
which is the strongest check a user has today for the PyPI path.
