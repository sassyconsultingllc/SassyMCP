---
name: sassymcp-shell
description: Use BEFORE calling sassy_shell, sassy_session_start, or sassy_session_send on a SassyMCP server. Establishes that the default shell is Windows PowerShell so commands are written in PowerShell syntax on the first try (no wasted retries on bash idioms). Also use when generating multi-line scripts to a SassyMCP target.
---

# SassyMCP Shell Context

The SassyMCP server runs on **Windows**. `sassy_shell` defaults to `shell="powershell"`. Write commands in PowerShell syntax up front — don't reach for bash habits unless you've explicitly switched to `shell="wsl"` or `shell="cmd"`.

## Quick translation

| bash / POSIX | PowerShell |
|---|---|
| `cd /d V:\foo` | `Set-Location V:\foo` |
| `cmd1 && cmd2` | `cmd1; cmd2` (or `if ($?) { cmd2 }`) |
| `cmd1 \|\| cmd2` | `if (-not $?) { cmd2 }` |
| `mkdir -p foo/bar` | `New-Item -ItemType Directory -Force foo/bar` |
| `cat foo` | `Get-Content foo` |
| `grep pat foo` | `Select-String pat foo` |
| `which X` | `Get-Command X` |
| `export FOO=bar` | `$env:FOO = "bar"` |
| `ls -la` | `Get-ChildItem -Force` |
| `rm -rf …` | intercepted — use `sassy_safe_delete` |
| escape with `\` | escape with backtick `` ` `` |

`sassy_shell` auto-rewrites `&&` → `;` and `cd /d` → `Set-Location` for the PowerShell shell, but writing it correctly the first time saves a round-trip.

## Multi-line scripts and file content

- Don't try to embed multi-line PowerShell or here-strings inside a single `sassy_shell` argument — quoting collapses, and `Set-Content @'...'@` content gets keyword-scanned even when it's just data.
- **Use `sassy_write_file(path, content)` instead** — direct .NET write, bypasses all PowerShell quoting and the shell-keyword scanner. Then invoke the resulting file with `sassy_shell`.

## When to switch shells

- `shell="cmd"`: `.bat` files, `tasklist`/`taskkill`, legacy CMD-only builtins.
- `shell="wsl"`: real bash — `grep`/`awk`/`sed` pipelines, Linux-native tools, anything where PowerShell equivalents are awkward. WSL receives the command verbatim, no PowerShell rewriting.
