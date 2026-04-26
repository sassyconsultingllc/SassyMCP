# personal/

Per-machine scripts that aren't safe to commit because they reference one
developer's specific layout — drive letters, mount points, named tunnels,
service names, etc.

This folder is **gitignored** (everything except this README and `.gitkeep`).
Drop your own machine-specific helpers here so they don't pollute the
public repo root.

## What typically lives here

- **`autostart-bridge.bat`** — wrapper invoked by Task Scheduler at logon
  to start the SassyMCP HTTP bridge. Knows about your drive layout (e.g.
  waits for a VeraCrypt-mounted drive to appear) and the absolute path to
  your `.venv\Scripts\python.exe`.
- **`register-autostart.ps1`** — installs the scheduled task that runs
  `autostart-bridge.bat`. Pins the script path in the task definition.
- **`status.bat`** — quick status check: drive mounted? venv present?
  bridge listening? Cloudflared running?
- **`start-tunnel.bat`** (vendor-flavored) — the production launcher for a
  specific named Cloudflare tunnel pointing at a specific hostname. The
  generic `start-tunnel.bat` that ships in the portable zip uses the
  bundled `sassymcp.exe` and accepts arbitrary tunnel names.

## Why these are personal

They reference things like:
- A specific drive letter (`V:`, `T:`, etc.) and absolute path
- A named Cloudflare tunnel UUID and hostname
- A specific cloudflared service install location
- Personal email / vendor identity strings

If you want to share one of these patterns with the project, copy the
file out, scrub the machine-specific bits, and submit it as a
configurable launcher under `deploy/` or `scripts/` instead.
