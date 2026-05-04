## vNEXT — Multi-client integration: auto-config CLI + DXT + VS Code extension

> **Draft**: bump `sassymcp/__init__.py` to a new version (suggested: `1.4.0` — additive, no breaking changes), then `git mv docs/releases/vNEXT-DRAFT.md docs/releases/v1.4.0.md` (or whatever you pick) before tagging.

### What changed

This release ends per-client JSON-editing as the install path. Three new entry points let any MCP client see SassyMCP without hand-editing config files; all three converge on a single shared brain in `~/.sassymcp/` (persona, memory, license, audit log) made safe for multi-process concurrency.

#### New install entry points

- **`sassymcp.dxt`** — Anthropic Desktop Extension format, double-click in Claude Desktop. On first launch, `sassymcp.exe` self-detects every OTHER MCP client on the box (Cursor, VS Code Copilot, Windsurf, Continue, Cline, Zed, Grok Desktop) and patches each one's config atomically. Marker file at `~/.sassymcp/.installed-other-clients` makes it idempotent.
- **VS Code extension** (Marketplace: `sassyconsultingllc.sassymcp`). Locates `sassymcp.exe` via PATH or the `sassymcp.exePath` setting, runs the install CLI, and adds a status bar item showing license tier + brain health. Five commands: Run Setup Wizard, Reinstall Configs, Open Audit Log, Open `_DELETE_` Folder, Show Brain Status. Setup Wizard webview mirrors `sassy_setup_wizard`'s questionnaire as a single-page form.
- **`sassymcp-install` CLI** — standalone command that detects all 8 supported clients and patches them atomically. `--dry-run`, `--uninstall`, `--client <one>`, `--exe-path <override>`, `--json`. Backups every existing config before its first edit (timestamped sibling).

#### Centralized brain — concurrency hardening

Every per-user state file in `~/.sassymcp/` is now safe for concurrent writes from multiple `sassymcp.exe` processes simultaneously:

- **SQLite databases** (`memory.db`, `crosslink.db`, `tool_state.db`) all open via a new `sassymcp._db.open_db()` helper that applies `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000ms`, and detects silent WAL rejection.
- **JSON config files** (`config.json`, `license.json`, `tokens.json`, `persona.md`) all write via `sassymcp._atomic.atomic_write_json` / `atomic_write_text` — same-dir tempfile + `os.replace` with a Windows-only PermissionError retry (50 × 10ms).
- **Audit log** (`audit.log`, `audit.jsonl`) writes via `sassymcp._audit_io.append_audit` — POSIX `O_APPEND` for kernel-level write serialization, Windows `msvcrt.locking` byte-0 lock with `LK_NBLCK` + retry (5s budget). fsync on every write on Windows for forensic durability.
- **`.license_secret` first-run race** fixed via `O_CREAT|O_EXCL` — exactly one process wins the create, the rest read the winner's value. Without this, multiple sassymcp.exe processes starting simultaneously would each generate divergent signing secrets and license validation would not agree across MCP clients.

These changes are invisible to single-user usage; their value is unlocked the moment a second MCP client connects to the same shared state directory.

#### Unified release pipeline

`.github/workflows/release.yml` produces all three artifacts on a `v*.*.*` tag push: `sassymcp.exe` (PyInstaller, Windows), `sassymcp.dxt` (zipped manifest + exe, Windows), and `sassymcp-vscode-<version>.vsix` (vsce package, Linux). The release job creates a GitHub Release with all three attached. Optional `publish-vsix` job pushes to the VS Code Marketplace if the `PUBLISH_VSIX` repo variable is set and `VSCE_PAT` is in repo secrets.

The CI refuses to build if the git tag doesn't match `__version__` in `sassymcp/__init__.py`.

### New files

- `sassymcp/_db.py`, `sassymcp/_atomic.py`, `sassymcp/_audit_io.py` — concurrency helpers
- `sassymcp/install.py` — auto-config CLI
- `dxt/manifest.json`, `dxt/README.md`, `dxt/icon.png`, `scripts/build-dxt.ps1` — DXT package
- `sassymcp-vscode/` — VS Code extension (TypeScript, ~654 LOC)
- `.github/workflows/release.yml` — unified release pipeline
- `scripts/gen-icons.py` — icon generator (192×192 PNG from the SVG source)
- `tests/test_db_helper.py`, `tests/test_atomic_write.py`, `tests/test_audit_io.py`, `tests/test_concurrency_integration.py`, `tests/test_concurrency_stress.py`, `tests/test_install.py` — 35 new tests

### Modified files (per-callsite migrations to the new helpers)

- `sassymcp/modules/memory.py`, `sassymcp/modules/state_manager.py` — `sqlite3.connect()` → `open_db()`
- `sassymcp/modules/crosslink.py` — 7 callsites migrated to `open_db()`
- `sassymcp/modules/audit.py` — 6 raw `f.write(json.dumps(...)+"\n")` → `append_audit()`
- `sassymcp/modules/runtime_config.py`, `sassymcp/modules/setup_wizard.py` — `path.write_text(json.dumps(...))` → `atomic_write_json()`
- `sassymcp/license.py` — atomic license.json writes + `O_CREAT|O_EXCL` for `.license_secret`
- `sassymcp/server.py` — `_maybe_run_first_run_install()` first-run hook for DXT
- `pyproject.toml` — new `sassymcp-install` script entry point + `[tool.pytest.ini_options]` slow marker
- `README.md`, `deploy/README.txt` — install sections lead with the new entry points

### Compatibility

- All existing `sassymcp` invocations (stdio, `--http`, `--tunnel`) work unchanged.
- Existing `~/.sassymcp/` data is read-compatible. Existing SQLite DBs are upgraded from `journal_mode=DELETE` to `WAL` on first open after upgrade (verified by the `test_open_db_switches_existing_delete_db_to_wal` test).
- Existing per-client configs (Claude Desktop, Cursor, etc.) are not touched until the user explicitly runs `sassymcp-install` or installs the DXT or VS Code extension. The first-run-install hook only fires when the marker file is absent.
- Concurrency-test suite includes a 30-second 8-process mixed-workload stress test marked `slow` (deselected by default; run with `pytest -m slow`).

### Migration notes

- **For existing users**: no action required. The new helpers and CLI are additive. Run `sassymcp-install --dry-run` if you want to see what the auto-config would do; nothing changes until you re-run without `--dry-run`.
- **For new users**: prefer the DXT (Claude Desktop) or the VS Code marketplace extension. Both auto-detect every other MCP client and patch them in one shot.
- **For LAN / Cloudflare Tunnel users**: nothing changes. Concurrency hardening protects the shared state files when multiple clients hit the same HTTP endpoint.

### Test coverage

- 52 unit + integration tests pass in ~32s on Windows + Python 3.14.3
- 1 stress test (8 processes × 30 seconds × 5 storage backends simultaneously) passes 4/4 runs, run with `pytest -m slow`
- See `docs/superpowers/specs/2026-05-03-multi-client-integration-design.md` for full architecture context.
