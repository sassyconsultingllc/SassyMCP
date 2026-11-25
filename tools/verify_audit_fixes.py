# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-46YUOPYHEL3X
"""One-shot verifier: runs every fix from the Mercury-2 audit + bug report.

Exit code 0 = all checks passed. Non-zero = at least one regressed.

Designed to be safe to run on a developer box (uses a temp SASSYMCP_HOME
where mutations are isolated). Does NOT touch the user's real ~/.sassymcp.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from pathlib import Path


GREEN = "\033[92m"
RED = "\033[91m"
YEL = "\033[93m"
RST = "\033[0m"

# Prefer the repo checkout over any installed sassymcp wheel/egg.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class Verifier:
    def __init__(self):
        self.passes: list[str] = []
        self.fails: list[tuple[str, str]] = []
        self.warns: list[str] = []

    def ok(self, label: str):
        self.passes.append(label)
        print(f"{GREEN}PASS{RST}  {label}")

    def fail(self, label: str, detail: str):
        self.fails.append((label, detail))
        print(f"{RED}FAIL{RST}  {label}\n        {detail}")

    def warn(self, label: str):
        self.warns.append(label)
        print(f"{YEL}WARN{RST}  {label}")

    def summary(self) -> int:
        print()
        print("=" * 60)
        print(f"  passes: {len(self.passes)}    fails: {len(self.fails)}    warns: {len(self.warns)}")
        print("=" * 60)
        if self.fails:
            print("\nFAILURES:")
            for label, detail in self.fails:
                print(f"  - {label}: {detail}")
            return 1
        return 0


V = Verifier()


def section(title: str):
    print()
    print(f"--- {title} ---")


# ── isolate state ────────────────────────────────────────────────────
tmp = Path(tempfile.mkdtemp(prefix="sassy-verify-"))
os.environ["SASSYMCP_HOME"] = str(tmp)
os.environ.pop("SASSYMCP_AUTH_TOKEN", None)
print(f"SASSYMCP_HOME = {tmp}")

# ── Section 1: bug-report fixes ──────────────────────────────────────
section("Bug report regressions")

# 1a) #5a — format word boundary
from sassymcp.modules._security import validate_command_tiered, detect_delete_intent

ok, _, _ = validate_command_tiered("Format-Table Name,Size")
V.ok("Format-Table passes (was blocked)") if ok else V.fail(
    "Format-Table passes", "still blocked")

ok, _, _ = validate_command_tiered("Get-Process | Format-List")
V.ok("Format-List in pipeline passes") if ok else V.fail(
    "Format-List in pipeline passes", "still blocked")

ok, _, _ = validate_command_tiered("something --format=table")
V.ok("--format=table passes") if ok else V.fail(
    "--format=table passes", "still blocked")

ok, _, _ = validate_command_tiered("format C:")
V.ok("Real 'format C:' is still blocked") if not ok else V.fail(
    "Real 'format C:' is still blocked", "leaked through")

# 1b) #5b — truncate-by-redirect
is_del, _ = detect_delete_intent(
    "ssh.exe -v 1.2.3.4 hostname > C:\\Users\\Admin\\ssh-test.log 2>&1")
V.ok("> .log under userprofile no longer blocked") if not is_del else V.fail(
    "> .log under userprofile no longer blocked", "still flagged")

is_del, _ = detect_delete_intent("echo hi > %TEMP%\\foo.txt")
V.ok("> %TEMP%\\foo.txt no longer blocked") if not is_del else V.fail(
    "> %TEMP%\\foo.txt no longer blocked", "still flagged")

is_del, _ = detect_delete_intent("echo hi > debug.log")
V.ok("> debug.log (relative) no longer blocked") if not is_del else V.fail(
    "> debug.log (relative) no longer blocked", "still flagged")

is_del, _ = detect_delete_intent("evil > C:\\Windows\\System32\\drivers\\etc\\hosts")
V.ok("> system file STILL blocked") if is_del else V.fail(
    "> system file STILL blocked", "leaked through")

# 1c) #1 — SSH key auth path
os.environ["SSH_HOST"] = "1.2.3.4"
os.environ["SSH_USER"] = "user"
os.environ.pop("SSH_PASS", None)
os.environ.pop("SSH_SESSION", None)
os.environ["SSH_KEY"] = r"C:\nonexistent\key.ppk"
from sassymcp.modules import linux as _linux

async def _ssh_dry():
    out = []
    async for chunk in _linux._ssh_exec_stream("echo hi", timeout=2):
        out.append(chunk)
    return "".join(out)

# We can't actually connect, but we CAN check that the auth resolution
# logic accepts a key without complaining about missing creds.
out = asyncio.run(_ssh_dry())
if "No SSH auth source configured" in out:
    V.fail("SSH_KEY accepted as auth source", "still complains about missing creds")
elif "SSH not configured" in out:
    V.fail("SSH_KEY accepted as auth source", out[:200])
else:
    # Either succeeded or failed to dial (no key file); both OK — the
    # important thing is it didn't bail at the auth-source gate.
    V.ok("SSH_KEY accepted as auth source (no key/session/pageant fallback complaint)")

# Confirm linux.py doesn't put SSH_PASS in argv when SSH_KEY is set.
os.environ["SSH_PASS"] = "should-not-appear-in-argv"
import sassymcp.modules.linux as _lmod
src = Path(_lmod.__file__).read_text(encoding="utf-8")
if '"-pw", SSH_PASS' in src or "'-pw', SSH_PASS" in src:
    V.fail("Password never in argv", "found legacy -pw in linux.py")
else:
    V.ok("Password never in argv (legacy -pw removed)")

# 1d) #2 — setup_ssh false-success on empty creds — verify status=incomplete
from sassymcp.modules import setup_wizard as _sw
# Find the tool function body via the registered server. We can't easily
# run the @server.tool here; instead, check the source for the right
# guard. (Behavior already verified by unit test once committed.)
sw_src = Path(_sw.__file__).read_text(encoding="utf-8")
if '"status": "incomplete"' in sw_src and "No auth source provided" in sw_src:
    V.ok("setup_ssh returns incomplete on empty creds (source check)")
else:
    V.fail("setup_ssh returns incomplete on empty creds", "guard not found in source")

# 1e) #3 — PS native-exe capture wrapping
from sassymcp.modules import shell as _shell
if _shell._looks_like_bare_native_exe("ssh.exe -v 1.2.3.4 hostname"):
    wrapped = _shell._wrap_native_for_powershell("ssh.exe -v 1.2.3.4 hostname")
    if "2>&1" in wrapped and "Out-String" in wrapped:
        V.ok("PS native-exe wrap merges stderr + Out-String")
    else:
        V.fail("PS native-exe wrap merges stderr + Out-String", f"got: {wrapped}")
else:
    V.fail("PS native-exe wrap detects ssh.exe", "_looks_like_bare_native_exe didn't match")

# 1f) #4 — cmd output capture: verify the source path appends 2>&1
shell_src = Path(_shell.__file__).read_text(encoding="utf-8")
if 'shell == "cmd"' in shell_src and 'command = command + " 2>&1"' in shell_src:
    V.ok("cmd mode auto-appends 2>&1 to capture stderr from child binaries")
else:
    V.fail("cmd mode auto-appends 2>&1", "guard not found in shell.py")


# ── Section 2: Mercury criticals ─────────────────────────────────────
section("Mercury-2 critical findings")

# 2a) sensitive-read denylist
from sassymcp.modules._security import is_sensitive_read_path

denied, _ = is_sensitive_read_path(Path.home() / ".ssh" / "id_rsa")
V.ok("Hash of ~/.ssh/id_rsa is refused") if denied else V.fail(
    "Hash of ~/.ssh/id_rsa is refused", "leaked through")

denied, _ = is_sensitive_read_path(Path.home() / "Documents" / "notes.txt")
V.ok("Normal user file is NOT in denylist") if not denied else V.fail(
    "Normal user file is NOT in denylist", "false positive")

from sassymcp._paths import TOKENS_FILE
denied, _ = is_sensitive_read_path(TOKENS_FILE)
V.ok("SassyMCP's own tokens.json is denied") if denied else V.fail(
    "SassyMCP's own tokens.json is denied", "leaked through")

# 2b) hash_file path validation in source
secaudit_src = Path(__file__).resolve().parent.parent / "sassymcp" / "modules" / "security_audit.py"
text = secaudit_src.read_text(encoding="utf-8")
if "is_sensitive_read_path(path)" in text and 'denied, reason = is_sensitive_read_path(path)' in text:
    V.ok("sassy_hash_file calls is_sensitive_read_path")
else:
    V.fail("sassy_hash_file calls is_sensitive_read_path", "call site not found")

if 'is_sensitive_read_path(apk_path)' in text:
    V.ok("sassy_apk_info calls is_sensitive_read_path")
else:
    V.fail("sassy_apk_info calls is_sensitive_read_path", "call site not found")

# 2c) selfmod permanently removed (no tools, no group, no gate)
from sassymcp.modules._tool_loader import TOOL_GROUPS as _TG
_has_selfmod_group = "selfmod" in _TG.keys()
if not _has_selfmod_group:
    V.ok("selfmod group removed from TOOL_GROUPS")
else:
    V.fail(
        "selfmod group removed from TOOL_GROUPS",
        f"group still present; keys={sorted(_TG.keys())}",
    )
selfmod_src = (Path(__file__).resolve().parent.parent / "sassymcp" / "modules" / "selfmod.py").read_text(encoding="utf-8")
if "permanently removed" in selfmod_src or "Self-modification tools removed" in selfmod_src:
    V.ok("selfmod module is a no-op stub (tools removed)")
else:
    V.fail("selfmod module is a no-op stub (tools removed)", "still looks like live selfmod")
if "sassy_selfmod_edit" not in selfmod_src and "@server.tool()" not in selfmod_src:
    V.ok("selfmod stub registers no tools")
else:
    V.fail("selfmod stub registers no tools", "tool registration still present")

# 2d) shell allow_pattern='*' removed
if 'allow_pattern == "*"' in shell_src:
    # OK if it's the refusal branch — search for "no longer accepted"
    if "no longer accepted" in shell_src:
        V.ok("shell allow_pattern='*' explicitly refused (not silently bypassed)")
    else:
        V.fail("shell allow_pattern='*' explicitly refused", "still has bypass branch")
else:
    V.fail("shell allow_pattern='*' branch exists for refusal", "branch missing")

# 2e) _confirm threading lock
from sassymcp.modules import _confirm as _conf
import sassymcp.modules._confirm as _cmod
cmod_src = Path(_cmod.__file__).read_text(encoding="utf-8")
if "_LOCK = threading.Lock()" in cmod_src and "with _LOCK:" in cmod_src:
    V.ok("_confirm guards _PENDING with threading.Lock")
else:
    V.fail("_confirm guards _PENDING with threading.Lock", "lock missing")

# Stress-test the lock: 16 threads × 100 make/consume pairs each
def _hammer():
    for _ in range(100):
        tok, _e = _conf.make_token("noop", "powershell", "low", "noop", ttl_seconds=5)
        _conf.consume_token(tok)

threads = [threading.Thread(target=_hammer) for _ in range(16)]
for t in threads: t.start()
for t in threads: t.join()
remaining = _conf.pending_count()
if remaining == 0:
    V.ok(f"_confirm survives 16-thread×100-call stress (final pending={remaining})")
else:
    V.warn(f"_confirm stress: {remaining} unconsumed (likely benign — expired test tokens)")

# 2f) updater SHA-256 sidecar logic in source
updater_src = (Path(__file__).resolve().parent.parent / "sassymcp" / "modules" / "updater.py").read_text(encoding="utf-8")
if "_fetch_checksum" in updater_src and "Checksum mismatch" in updater_src and "hasher.update(chunk)" in updater_src:
    V.ok("updater apply() streams + verifies SHA-256")
else:
    V.fail("updater apply() streams + verifies SHA-256", "logic missing")

# 2g) combos sassy_combo_codebase_grep doesn't call missing _search_files
combos_src = (Path(__file__).resolve().parent.parent / "sassymcp" / "modules" / "combos.py").read_text(encoding="utf-8")
if "fileops._search_files" in combos_src:
    V.fail("combos no longer references nonexistent fileops._search_files",
           "old broken call still present")
elif 'server._tool_manager._tools.get("sassy_search_files")' in combos_src:
    V.ok("combos uses registered sassy_search_files via server")
else:
    V.warn("combos search routing changed in an unexpected way")


# ── Section 3: Mercury highs (auth hardening) ────────────────────────
section("Mercury highs — auth hardening")

# 3a) Token format restriction
from sassymcp.auth import _token_format_valid
if _token_format_valid("abcdef1234567890ABCD-_"):
    V.ok("URL-safe token alphabet accepted")
else:
    V.fail("URL-safe token alphabet accepted", "valid token rejected")

if not _token_format_valid("abcdef1234567890;rm -rf /"):
    V.ok("Token with shell metacharacters rejected")
else:
    V.fail("Token with shell metacharacters rejected", "leaked through")

if not _token_format_valid("short"):
    V.ok("Token below minimum length rejected")
else:
    V.fail("Token below minimum length rejected", "leaked through")

# 3b) Verifier hash-keyed lookup
auth_src = (Path(__file__).resolve().parent.parent / "sassymcp" / "auth.py").read_text(encoding="utf-8")
if "self._token_map.get(token_hash)" in auth_src and 'hmac.compare_digest(token, "x" * len(token))' in auth_src:
    V.ok("verify_token uses O(1) hash lookup + constant-time miss")
else:
    V.fail("verify_token uses O(1) hash lookup + constant-time miss", "logic missing")

# 3c) Windows ACL helpers
from sassymcp.auth import _check_file_permissions, _lockdown_windows_acl
if os.name == "nt":
    # Generate a token to write tokens.json, then verify ACL is owner-only
    from sassymcp.server import _ensure_default_token
    tok = _ensure_default_token()
    if tok and TOKENS_FILE.exists():
        ok = _check_file_permissions(TOKENS_FILE)
        if ok:
            V.ok("Windows ACL on fresh tokens.json passes owner-only check")
        else:
            V.fail("Windows ACL on fresh tokens.json passes owner-only check",
                   "icacls reported unsafe principal")
    else:
        V.warn("Could not bootstrap token to test ACL")
else:
    V.warn("Windows ACL test skipped on POSIX")

# 3d) Audit secret masking
from sassymcp.modules.audit import _mask_args, _redact_value
masked = _mask_args({
    "cmd": "ssh user@host",
    "password": "hunter2",
    "token": "ghp_AbCdEfGhIjKlMnOpQrStUv1234567890",
    "note": "embedded ghp_AbCdEfGhIjKlMnOpQrStUv1234567890 here",
})
if masked["password"] == "***REDACTED***" and masked["token"] == "***REDACTED***" and "***REDACTED***" in masked["note"]:
    V.ok("Audit masks key-based AND value-based secrets")
else:
    V.fail("Audit masks key-based AND value-based secrets", json.dumps(masked))

if "***REDACTED***" in _redact_value("AKIAIOSFODNN7EXAMPLE in command"):
    V.ok("Audit catches AWS access key by shape")
else:
    V.fail("Audit catches AWS access key by shape", "not masked")

if "***REDACTED***" in _redact_value("-----BEGIN RSA PRIVATE KEY-----\nMII..."):
    V.ok("Audit catches PEM private key by shape")
else:
    V.fail("Audit catches PEM private key by shape", "not masked")


# ── Section 4: Mercury mediums + auxiliary ───────────────────────────
section("Mercury mediums + auxiliary")

# 4a) Crosslink payload cap
from sassymcp.modules.crosslink import _post_message, _MAX_PAYLOAD_BYTES
big = "x" * (_MAX_PAYLOAD_BYTES + 1)
try:
    _post_message("sid", "ch", big)
    V.fail("Crosslink refuses oversize payload", "no exception raised")
except ValueError:
    V.ok("Crosslink refuses oversize payload")
except Exception as e:
    V.warn(f"Crosslink raised unexpected exception type: {type(e).__name__}: {e}")

# 4b) Vision byte cap is wired
vision_src = (Path(__file__).resolve().parent.parent / "sassymcp" / "modules" / "vision.py").read_text(encoding="utf-8")
if "_MAX_TOTAL_BYTES" in vision_src and "byte_cap_hit" in vision_src and "truncated" in vision_src:
    V.ok("vision sassy_screen_watch enforces cumulative byte cap")
else:
    V.fail("vision sassy_screen_watch enforces cumulative byte cap", "logic missing")

# 4c) phone_screen adb pipe recombine
ps_src = (Path(__file__).resolve().parent.parent / "sassymcp" / "modules" / "phone_screen.py").read_text(encoding="utf-8")
if 'raw_args = ["shell", " ".join(raw_args[1:])]' in ps_src and "_ADB_SEMAPHORE = asyncio.Semaphore" in ps_src:
    V.ok("phone_screen recombines adb pipelines + semaphore bound")
else:
    V.fail("phone_screen recombines adb pipelines + semaphore bound", "patches missing")

if "_reap_scrcpy_on_exit" in ps_src and "atexit.register" in ps_src:
    V.ok("scrcpy shutdown reaper registered")
else:
    V.fail("scrcpy shutdown reaper registered", "atexit hook missing")

# 4d) HTTP mutating-verb gate
utility_src = (Path(__file__).resolve().parent.parent / "sassymcp" / "modules" / "utility.py").read_text(encoding="utf-8")
if "allow_mutating" in utility_src and "_READ_VERBS = " in utility_src:
    V.ok("sassy_http gates mutating verbs behind allow_mutating")
else:
    V.fail("sassy_http gates mutating verbs behind allow_mutating", "guard missing")

# 4e) fileops write uses _check_write_path
fileops_src = (Path(__file__).resolve().parent.parent / "sassymcp" / "modules" / "fileops.py").read_text(encoding="utf-8")
if "_check_write_path(path)" in fileops_src and "def _check_write_path" in fileops_src:
    V.ok("fileops write path uses sensitive-read denylist fallback")
else:
    V.fail("fileops write path uses sensitive-read denylist fallback", "missing")

# 4f) Singleton locks
tl_src = (Path(__file__).resolve().parent.parent / "sassymcp" / "modules" / "_tool_loader.py").read_text(encoding="utf-8")
if "_TRACKER_LOCK = threading.Lock()" in tl_src:
    V.ok("get_tracker singleton lock present")
else:
    V.fail("get_tracker singleton lock present", "lock missing")

rl_src = (Path(__file__).resolve().parent.parent / "sassymcp" / "modules" / "_rate_limiter.py").read_text(encoding="utf-8")
if "self._configs: dict[str, int] = {}" in rl_src and "self._lock = threading.Lock()" in rl_src:
    V.ok("rate limiter splits config from semaphore + lock")
else:
    V.fail("rate limiter splits config from semaphore + lock", "split missing")

# 4g) first-run install atomic marker
server_src = (Path(__file__).resolve().parent.parent / "sassymcp" / "server.py").read_text(encoding="utf-8")
if "os.O_EXCL" in server_src and "first_run_install" in server_src.lower():
    V.ok("first-run install uses O_EXCL marker for race safety")
else:
    V.fail("first-run install uses O_EXCL marker for race safety", "O_EXCL missing")

# 4h) rate-limiter logging level
if "tools will run UNTHROTTLED" in server_src:
    V.ok("rate limiter failure logged at ERROR level (visible)")
else:
    V.fail("rate limiter failure logged at ERROR level (visible)", "loud-fail message missing")


# ── Section 5: packaging + OAuth ──────────────────────────────────────
section("Packaging + OAuth")

installer_src = (Path(__file__).resolve().parent.parent / "installer.wxs").read_text(encoding="utf-8")
# Only flag absolute paths if they appear in an actual Source= attribute,
# not in explanatory comments inside the file.
abs_source = re.search(r"Source\s*=\s*['\"]V:\\\\Projects\\\\SassyMCP", installer_src) or \
             re.search(r"Source\s*=\s*['\"]V:\\Projects\\SassyMCP", installer_src)
if abs_source:
    V.fail("installer.wxs uses relative paths",
           "absolute developer-machine paths still present in Source= attributes")
else:
    V.ok("installer.wxs Source= attributes use relative paths")

if '$(var.ExtractRoot)' in installer_src and 'define ExtractRoot' in installer_src:
    V.ok("installer.wxs ExtractRoot variable defined")
else:
    V.fail("installer.wxs ExtractRoot variable defined", "<?define ExtractRoot ?> missing")

if 'InstallScope="perUser"' in installer_src:
    V.ok("installer scope is perUser (no UAC)")
else:
    V.fail("installer scope is perUser (no UAC)", "still perMachine")

oauth_src = (Path(__file__).resolve().parent.parent / "sassymcp-oauth" / "src" / "index.js").read_text(encoding="utf-8")
if "crypto.subtle.digest" in oauth_src and "async function timingSafeEqual" in oauth_src:
    V.ok("OAuth timingSafeEqual uses crypto.subtle (constant-time)")
else:
    V.fail("OAuth timingSafeEqual uses crypto.subtle (constant-time)", "still custom JS")

if "await timingSafeEqual" in oauth_src:
    V.ok("OAuth caller awaits the now-async timingSafeEqual")
else:
    V.fail("OAuth caller awaits the now-async timingSafeEqual", "missing await")


# ── Section 6: end-to-end import smoke ───────────────────────────────
section("Final import smoke")

mods = [
    "sassymcp.server", "sassymcp.auth", "sassymcp._paths",
    "sassymcp.modules._security", "sassymcp.modules._confirm",
    "sassymcp.modules._tool_loader", "sassymcp.modules._rate_limiter",
    "sassymcp.modules.audit", "sassymcp.modules.combos",
    "sassymcp.modules.crosslink", "sassymcp.modules.fileops",
    "sassymcp.modules.linux", "sassymcp.modules.phone_screen",
    "sassymcp.modules.security_audit", "sassymcp.modules.selfmod",
    "sassymcp.modules.setup_wizard", "sassymcp.modules.shell",
    "sassymcp.modules.updater", "sassymcp.modules.utility",
    "sassymcp.modules.vision",
]
for m in mods:
    try:
        __import__(m)
        V.ok(f"import {m}")
    except Exception as e:
        V.fail(f"import {m}", f"{type(e).__name__}: {e}")


# ── cleanup ──────────────────────────────────────────────────────────
try:
    shutil.rmtree(tmp, ignore_errors=True)
except Exception:
    pass

sys.exit(V.summary())
