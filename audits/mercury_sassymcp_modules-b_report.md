```json
{
  "severity": "high",
  "category": "correctness",
  "file": "sassymcp/modules/phone_screen.py",
  "symbol": "sassy_phone_state",
  "issue": "ADB command strings contain pipe characters ('|') passed as separate arguments, but adb does not interpret them as shell pipes, so the intended filtering never occurs.",
  "why": "The tool returns empty or incorrect state information, breaking downstream logic that relies on screen state, and may cause the AI to make unsafe decisions based on stale data.",
  "fix": "Replace the pipe‑based commands with a single shell command string executed via `adb shell` (e.g. `adb shell \"dumpsys activity | grep -E ...\"`) or perform the filtering in Python after capturing the full output.",
  "confidence": 0.95
}
{
  "severity": "critical",
  "category": "security",
  "file": "sassymcp/modules/security_audit.py",
  "symbol": "sassy_hash_file",
  "issue": "The tool reads an arbitrary file path supplied by the LLM client and returns its hash without any path validation or sandboxing.",
  "why": "An attacker‑controlled LLM can request hashes of any file on the host, exfiltrating secrets such as SSH keys, password files, or proprietary source code.",
  "fix": "Validate the path with `_validate_path` and reject any path that is outside a configured whitelist or is marked as protected by `is_protected_path`. Return an error instead of performing the hash.",
  "confidence": 0.99
}
{
  "severity": "critical",
  "category": "security",
  "file": "sassymcp/modules/security_audit.py",
  "symbol": "sassy_apk_info",
  "issue": "Accepts an arbitrary `apk_path` argument and reads the file (or runs `aapt`) without any validation.",
  "why": "A malicious LLM can point to any file on the filesystem (e.g., `/etc/passwd`) and cause its contents to be returned, leaking sensitive data.",
  "fix": "Apply the same `_validate_path`/`is_protected_path` checks as in other file‑access tools, and limit the operation to a known directory of APKs or require explicit user confirmation.",
  "confidence": 0.98
}
{
  "severity": "high",
  "category": "security",
  "file": "sassymcp/modules/setup_wizard.py",
  "symbol": "sassy_setup_ssh",
  "issue": "Passes the SSH password on the command line to `plink`, which can be read from the process list by other users.",
  "why": "Passwords exposed via command‑line arguments can be captured by a privileged attacker or by other processes on the same machine, compromising remote hosts.",
  "fix": "Use `plink`'s `-batch` mode with password supplied via stdin or a secure credential store, and avoid including the password in the argument list. If that is not possible, refuse to accept a password argument and require the user to provide it interactively.",
  "confidence": 0.93
}
{
  "severity": "critical",
  "category": "mcp tool safety",
  "file": "sassymcp/modules/selfmod.py",
  "symbol": "sassy_selfmod_edit",
  "issue": "Allows arbitrary edits to any Python file within the project root, including core files, without any user confirmation or restriction.",
  "why": "An adversarial LLM can modify core server code (e.g., authentication, sandboxing) and then trigger a restart, achieving full remote code execution under the user's privileges.",
  "fix": "Restrict edits to the `modules/` directory only, require an explicit `confirm='YES'` flag, and log every edit to an immutable audit trail. Disallow edits to files that are not marked as hot‑reloadable.",
  "confidence": 0.97
}
{
  "severity": "critical",
  "category": "mcp tool safety",
  "file": "sassymcp/modules/selfmod.py",
  "symbol": "sassy_selfmod_write",
  "issue": "Writes arbitrary content to any file within the project root without confirmation, and then hot‑reloads the module.",
  "why": "An attacker can replace a module with malicious code, which will be immediately loaded and executed, compromising the entire server.",
  "fix": "Require a `confirm='YES'` flag, validate the target path against a whitelist of allowed module files, and perform a dry‑run syntax check before overwriting. Log the operation with a signed audit entry.",
  "confidence": 0.96
}
{
  "severity": "high",
  "category": "security",
  "file": "sassymcp/modules/shell.py",
  "symbol": "sassy_shell",
  "issue": "Executes PowerShell commands by passing a raw command string to `powershell.exe -Command`, with only a tiered blocklist for destructive patterns. The allow‑pattern bypass can be used to execute arbitrary code, and the blocklist does not prevent command injection via PowerShell features (e.g., `Invoke-Expression`).",
  "why": "A malicious LLM can run any PowerShell script, download and execute payloads, or modify the system, leading to full compromise.",
  "fix": "Run commands in a sandboxed environment, enforce a strict whitelist of allowed commands, and disable the `allow_pattern` bypass unless explicitly authorized. Consider using PowerShell's `-EncodedCommand` with a signed payload, or reject any command containing potentially dangerous constructs (e.g., `Invoke-Expression`, `Start-Process`).",
  "confidence": 0.94
}
{
  "severity": "medium",
  "category": "security",
  "file": "sassymcp/modules/shell.py",
  "symbol": "sassy_shell",
  "issue": "The `allow_pattern` parameter can be set to `*` by the LLM, bypassing all pattern‑based safety checks.",
  "why": "An attacker can deliberately set `allow_pattern='*'` to execute any command, including destructive ones, without triggering the interceptor.",
  "fix": "Remove the wildcard bypass or require a separate elevated‑privilege token to use it. Log any use of `allow_pattern` with the supplied pattern for audit.",
  "confidence": 0.88
}
{
  "severity": "medium",
  "category": "security",
  "file": "sassymcp/modules/http.py",
  "symbol": "sassy_http",
  "issue": "Allows arbitrary HTTP methods and request bodies without restricting to safe verbs.",
  "why": "An attacker could issue `DELETE`, `PUT`, or `POST` requests to internal services, causing unintended side‑effects or data loss.",
  "fix": "Whitelist safe methods (`GET`, `HEAD`, optionally `POST` with read‑only endpoints) and reject others unless the caller provides an explicit `allow_method` flag that is logged and audit‑checked.",
  "confidence": 0.85
}
{
  "severity": "high",
  "category": "security",
  "file": "sassymcp/modules/updater.py",
  "symbol": "sassy_update_apply",
  "issue": "Downloads update assets over HTTPS but does not verify a digital signature or checksum before returning the path to the user.",
  "why": "A man‑in‑the‑middle or compromised GitHub release could deliver a malicious installer, which the user may run, leading to full system compromise.",
  "fix": "Publish a SHA‑256 checksum or PGP signature with each release, fetch it alongside the asset, verify it locally, and only return the path if verification succeeds. Abort and alert on mismatch.",
  "confidence": 0.92
}
{
  "severity": "low",
  "category": "concurrency",
  "file": "sassymcp/modules/phone_screen.py",
  "symbol": "_adb",
  "issue": "No limit on concurrent ADB subprocesses; rapid repeated calls could spawn many processes and exhaust system resources.",
  "why": "An adversarial LLM could cause a denial‑of‑service by repeatedly invoking tools that call `_adb` (e.g., `sassy_phone_ui`).",
  "fix": "Introduce a semaphore (e.g., `asyncio.Semaphore`) to cap concurrent ADB calls (e.g., max 3) and queue excess requests.",
  "confidence": 0.78
}
{
  "severity": "low",
  "category": "resource / lifecycle",
  "file": "sassymcp/modules/phone_screen.py",
  "symbol": "sassy_scrcpy_start",
  "issue": "Stores the scrcpy subprocess in a global variable `_scrcpy_proc` but never checks for process termination on server shutdown, potentially leaving a dangling process.",
  "why": "Orphaned scrcpy instances consume CPU and may keep the device connection alive, leading to resource leakage.",
  "fix": "Register a shutdown hook that terminates `_scrcpy_proc` if still running, and clear the variable after stopping.",
  "confidence": 0.81
}
{
  "severity": "low",
  "category": "api / idiomatic",
  "file": "sassymcp/modules/vision.py",
  "symbol": "sassy_screen_watch",
  "issue": "The function uses a hard‑coded `max_frames` limit of 15 but does not enforce a maximum total byte size for the returned frames.",
  "why": "A malicious LLM could request a very long duration with a high `max_frames` and large resolution, causing the response to exceed MCP token limits and potentially cause out‑of‑memory errors.",
  "fix": "Add a cumulative size cap (e.g., 500 KB) and truncate or drop frames once the cap is reached, returning a warning to the client.",
  "confidence": 0.73
}
```

### Top 5 things to fix before release
1. **Secure file‑access tools** – `sassy_hash_file` and `sassy_apk_info` must enforce path validation and protect sensitive files.  
2. **Harden self‑modification** – `sassy_selfmod_edit`/`write` should be limited to hot‑reloadable modules, require explicit confirmation, and be logged.  
3. **Fix PowerShell command execution** – `sassy_shell` must eliminate the wildcard bypass, enforce a strict whitelist, and prevent dangerous constructs like `Invoke‑Expression`.  
4. **Validate ADB command pipelines** – `sassy_phone_state` (and similar) should construct shell pipelines correctly or perform filtering in Python.  
5. **Add integrity checks to updates** – `sassy_update_apply` must verify signatures or checksums before exposing the downloaded installer.

### Release‑readiness verdict
**hold** – the critical security and safety issues identified (especially the unrestricted file reads, self‑modification, and PowerShell execution) must be addressed before the installer can be safely shipped.