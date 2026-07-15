<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-SassyMCP-SFQRQWRRZARE
-->
```json
{
  "severity": "high",
  "category": "SECURITY",
  "file": "sassymcp/auth.py",
  "symbol": "_check_file_permissions",
  "issue": "Windows token file permission check is a no‑op, allowing world‑readable tokens.",
  "why": "An attacker with a non‑admin account could read ~/.sassymcp/tokens.json and steal bearer tokens, enabling remote LLM clients to impersonate the user.",
  "fix": "Implement proper Windows ACL checks (e.g., using `os.stat` and `win32security` to ensure only the owner has read access) or reject token files that are not explicitly restricted.",
  "confidence": 0.95
}
{
  "severity": "high",
  "category": "SECURITY",
  "file": "sassymcp/server.py",
  "symbol": "_ensure_default_token",
  "issue": "Bootstrap token is written to tokens.json without restrictive file permissions on Windows.",
  "why": "The generated token may be readable by any user on the machine, allowing them to extract the bearer token and bypass authentication.",
  "fix": "After atomic_write_json, explicitly set the file mode to 0o600 on POSIX and use `os.chmod`/Windows ACL APIs to restrict access on Windows.",
  "confidence": 0.9
}
{
  "severity": "high",
  "category": "SECURITY",
  "file": "sassymcp/server.py",
  "symbol": "_print_banner",
  "issue": "Full bearer token is emitted in the startup banner (stdout) for copy‑and‑paste.",
  "why": "If the server is started from a script, service, or CI pipeline where stdout is logged, the token will be captured in logs, leaking credentials to any party with log access.",
  "fix": "Only display a placeholder in the banner and require the user to retrieve the token via `sassymcp.exe show-token`; never print the raw token to stdout.",
  "confidence": 0.92
}
{
  "severity": "critical",
  "category": "PACKAGING / INSTALL / UPDATER",
  "file": "installer.wxs",
  "symbol": null,
  "issue": "All File components reference absolute source paths on the developer's machine (e.g., V:\\Projects\\SassyMCP\\dist\\Most recent extract\\...).",
  "why": "When the MSI is built on a different machine, those source files will not exist, causing the installer to fail or embed empty files, breaking the deployment and potentially leaving a partially‑installed, vulnerable state.",
  "fix": "Replace the absolute paths with relative paths using the Wix `Source` attribute pointing to files in the project directory (e.g., `Source='..\dist\Most recent extract\...')` and ensure the build process copies the required files into the source tree before candle/light runs.",
  "confidence": 0.99
}
{
  "severity": "high",
  "category": "PACKAGING / INSTALL / UPDATER",
  "file": "installer.wxs",
  "symbol": null,
  "issue": "The MSI is configured with `InstallScope=\"perMachine\"`, which forces elevated privileges for installation.",
  "why": "Running the installer as admin gives the MCP server system‑wide write access, allowing any user on the machine to trigger the installer (or a malicious version) and gain elevated execution of the SassyMCP binaries, facilitating privilege escalation.",
  "fix": "Change `InstallScope` to `perUser` (default) so the installer runs without admin rights, and only request elevation when truly necessary (e.g., when installing system services).",
  "confidence": 0.94
}
{
  "severity": "medium",
  "category": "CONCURRENCY",
  "file": "sassymcp/server.py",
  "symbol": "_maybe_run_first_run_install",
  "issue": "Spawns a detached subprocess to patch client configs without synchronisation, potentially racing with the main process’s token bootstrap.",
  "why": "Concurrent writes to the same config files could interleave, causing corrupted JSON or loss of the generated bearer token, which may lead to authentication failures or unintended privilege exposure.",
  "fix": "Use a file‑based lock (e.g., `fasteners.InterProcessLock`) around both the bootstrap token write and the auto‑install subprocess, or make the subprocess wait for the token file to be safely written before proceeding.",
  "confidence": 0.85
}
{
  "severity": "low",
  "category": "CONCURRENCY",
  "file": "sassymcp/server.py",
  "symbol": "_setup_rate_limiter",
  "issue": "Catches all exceptions and silently disables rate limiting by returning `None`.",
  "why": "If the rate‑limiter fails to initialise, all tools become unthrottled, allowing an adversarial LLM client to flood the server and potentially cause denial‑of‑service.",
  "fix": "Log the exception at `error` level and abort startup or fall back to a safe default limiter that enforces a minimal per‑group limit.",
  "confidence": 0.78
}
{
  "severity": "low",
  "category": "SECURITY",
  "file": "sassymcp/auth.py",
  "symbol": "_token_format_valid",
  "issue": "Token validation only checks printable characters and length, allowing characters such as backticks or shell‑special symbols.",
  "why": "If a token containing shell‑meta characters is later used in a `subprocess` call (e.g., in a custom tool), it could lead to command injection when the token is interpolated into a command string.",
  "fix": "Restrict tokens to a strict URL‑safe Base64 alphabet (`[A-Za-z0-9\-_]`) and reject any other characters.",
  "confidence": 0.7
}
{
  "severity": "low",
  "category": "API / IDIOMATIC",
  "file": "sassymcp/server.py",
  "symbol": "_print_banner",
  "issue": "Uses `f\"Bearer {token[:6]}...{token[-4:]}\"` without handling tokens shorter than 10 characters, which could expose the whole token.",
  "why": "A short token would be displayed in full, increasing leakage risk.",
  "fix": "Add a guard: if `len(token) < 10` display a masked placeholder instead of the full token.",
  "confidence": 0.6
}
{
  "severity": "medium",
  "category": "SECURITY",
  "file": "sassymcp/auth.py",
  "symbol": "SassyTokenVerifier.verify_token",
  "issue": "Iterates over all stored tokens for each verification, performing a timing‑safe compare for each entry.",
  "why": "With a large number of tokens this can cause noticeable latency, potentially leading to time‑outs that an attacker could exploit to cause denial‑of‑service.",
  "fix": "Store a hash of each token (e.g., SHA‑256) as the key and perform a single constant‑time lookup; only compare the raw token when the hash matches.",
  "confidence": 0.8
}
{
  "severity": "low",
  "category": "CONCURRENCY",
  "file": "sassymcp/_audit_io.py",
  "symbol": "append_audit",
  "issue": "On POSIX the atomic append uses `os.open` with `O_APPEND` but does not lock the file; on some NFS implementations this may not be truly atomic.",
  "why": "Concurrent writes could interleave, corrupting the audit log and making forensic analysis unreliable.",
  "fix": "Add an advisory file lock (`fcntl.flock`) around the write on POSIX to guarantee serialization across NFS mounts.",
  "confidence": 0.65
}
{
  "severity": "medium",
  "category": "AUTH / OAUTH / LICENSE",
  "file": "sassymcp-oauth/src/index.js",
  "symbol": "handleAuthorize",
  "issue": "The `pre_auth_secret` is compared using a custom timing‑safe function that runs in JavaScript and may be vulnerable to side‑channel attacks in the Cloudflare Workers environment.",
  "why": "An attacker could perform a timing analysis to guess the secret, allowing unauthorized issuance of tokens.",
  "fix": "Replace the custom `timingSafeEqual` with `crypto.subtle.digest` based constant‑time comparison (e.g., compare SHA‑256 hashes) to avoid side‑channel leakage.",
  "confidence": 0.78
}
```

**Top 5 things to fix before release**
1. **Token file permissions on Windows** – `auth._check_file_permissions` and the bootstrap routine must enforce strict ACLs; otherwise bearer tokens can be read by any user.
2. **Full token leakage in startup banner** – `_print_banner` prints the raw bearer token to stdout, risking exposure in logs or console recordings.
3. **Installer source paths** – `installer.wxs` uses absolute developer‑machine paths, breaking the MSI on any other machine; replace them with relative paths.
4. **Per‑machine install scope** – The MSI forces admin rights (`InstallScope="perMachine"`); switch to per‑user to avoid unnecessary privilege escalation.
5. **Windows token file ACL check omitted** – `_check_file_permissions` returns `True` on Windows without actually verifying file security; implement proper Windows ACL verification.

**Release‑readiness verdict:** **hold** – critical security and packaging defects must be addressed before the product can be safely shipped.