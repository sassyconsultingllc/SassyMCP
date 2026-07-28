# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-TJV46I6FKKYT
"""Sandbox tests for the delete interceptor and related guards.

Run with the project's Python interpreter:
    V:\\tools\\python\\python.exe V:\\Projects\\SassyMCP\\_test_intercept.py
"""
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"V:\Projects\SassyMCP")

from sassymcp.modules._security import detect_delete_intent, is_protected_path
from sassymcp.modules.shell import (
    _parse_delete_targets,
    _safe_move_to_staging,
    _STAGING_FOLDER,
)

# Avoid literal 'Remove-Item' / 'del' as the first-word of any run command,
# because this file may be executed via sassy_shell itself which would trip
# the legacy interceptor.
RI = "R" + "emove-Item"
CC = "C" + "lear-Content"
NETD = "[System.IO.File]::D" + "elete"

PASS = 0
FAIL = 0


def check(label, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"OK   {label}")
    else:
        FAIL += 1
        print(f"FAIL {label}  {extra}")


# ── detect_delete_intent ─────────────────────────────────────────────
print("\n[1] detect_delete_intent")
cases = [
    # v1.1.1 cases (must still pass)
    ("del foo.txt", True),
    (RI + " foo", True),
    ("rm foo", True),
    ('powershell -c "del foo"', True),
    ("cmd /c del foo", True),
    ("ri foo", True),
    ("gci *.tmp | ri", True),
    ("gci *.tmp | " + RI, True),
    ("Get-ChildItem *.tmp | " + RI + " -Force", True),
    ("rd /s /q foo", True),
    (CC + " foo.txt", True),
    (NETD + '("foo")', True),
    ("sdelete -p 3 foo", True),
    ('bash -c "rm foo"', True),
    ("wsl -- rm foo", True),
    ("Get-ChildItem foo", False),
    ("echo hello", False),
    ("mv foo bar", False),

    # v1.1.2 NEW cases
    ("Out-File -Force target.txt", True),                           # D1
    ("type foo > bar.dll", True),                                   # D2 mid-command redirect (non-exempt ext; .txt/.log are exempt)
    ("Get-Content foo | Out-File -Force bar", True),                # D3 pipeline to Out-File
    ("New-Item -Force existing.txt", True),                         # D4 was previously False
    ("robocopy V:\\src V:\\dst /MIR", True),                        # C3 tree-wipe
    ("robocopy V:\\src V:\\dst /PURGE", True),                      # C3 purge
    ("copy /y foo bar", True),                                      # D5 copy /y
    ("xcopy /y /e foo bar", True),                                  # D5 xcopy /y
    ("powershell -EncodedCommand ZABlAGwAIABmAG8AbwA=", True),      # D6 base64 "del foo"
    ("$null = ri foo", True),                                       # D7 prefix strip

    # v1.1.2 NEGATIVES (must stay False — no regressions)
    ("Set-Content -Path foo -Value bar", False),                    # D8 false-positive fix
    ("Set-Content -Path foo -Value hello world", False),            # D8
    ("Out-File target.txt", False),                                 # Out-File WITHOUT -Force ok
    ("New-Item foo.txt", False),                                    # New-Item WITHOUT -Force ok
    ("New-Item -ItemType Directory -Path foo -Force", False),       # idempotent dir create ok
    ("New-Item -ItemType Directory foo -Force", False),             # positional path, dir create ok
    ("New-Item -Force -ItemType Directory foo", False),             # flag-order variation
    ("New-Item -ItemType SymbolicLink -Path l -Target t -Force", False),  # symlink create ok
    ("New-Item -ItemType Junction -Path j -Target t -Force", False),     # junction create ok
    ("copy foo bar", False),                                        # copy WITHOUT /y ok
    ("robocopy V:\\src V:\\dst", False),                            # plain robocopy ok
    ("echo foo >> bar.txt", False),                                 # append redirect ok
    ("command 2> errors.log", False),                               # stderr redirect ok
]
for cmd, expected in cases:
    got, kw = detect_delete_intent(cmd)
    check(f"detect {expected}  <- {cmd!r}", got == expected, f"got=({got},{kw!r})")


# ── is_protected_path ────────────────────────────────────────────────
print("\n[2] is_protected_path")
protected_cases = [
    (r"V:\Projects\SassyMCP\sassymcp\modules\shell.py", True),
    (r"V:\Projects\SassyMCP\_DELETE_", True),
    (r"V:\Projects\SassyMCP\_DELETE_\old.txt", False),       # inside staging is OK
    (r"C:\Users\Admin\foo.txt", False),
    (str(Path.home() / ".sassymcp" / "audit.log"), True),

    # v1.1.2 NEW: traversal bypasses must be caught by resolve()
    (r"V:\Projects\SassyMCP\sassymcp\modules\..\modules\shell.py", True),       # R0 dot-traversal inside
    (r"V:\Projects\SassyMCP\_DELETE_\..\sassymcp\modules\shell.py", True),      # R1 _DELETE_ escape
    (r"V:\Projects\SassyMCP\..\SassyMCP\sassymcp\modules\shell.py", True),      # R2 sibling traversal
    # Legit staging subfolder (must stay unprotected)
    (r"V:\Projects\SassyMCP\sassymcp\modules\_DELETE_\old.py", False),          # module-level staging ok
]
for path, expected in protected_cases:
    got, reason = is_protected_path(path)
    check(f"protected={expected} <- {path}", got == expected, f"got=({got},{reason!r})")


# ── _parse_delete_targets: Windows path preservation ────────────────
print("\n[3] _parse_delete_targets — Windows paths & flags")
parse_cases = [
    ("del C:\\Users\\foo\\bar.txt",       ["C:\\Users\\foo\\bar.txt"]),
    ("del foo.txt bar.txt",               ["foo.txt", "bar.txt"]),
    ("rm -rf foo",                        ["foo"]),
    ("rd /s /q C:\\temp\\garbage",        ["C:\\temp\\garbage"]),
    ("Remove-Item -Path foo -Recurse",    ["foo"]),
    # POSIX absolute path must NOT be classified as a CMD flag.
    ("rm /tmp/foo",                       ["/tmp/foo"]),
]
for cmd, expected in parse_cases:
    got = _parse_delete_targets(cmd)
    check(f"parse {cmd!r} -> {expected}", got == expected, f"got={got}")


# ── _safe_move_to_staging: end-to-end in a sandbox ──────────────────
print("\n[4] _safe_move_to_staging — sandbox")

async def test_staging():
    with tempfile.TemporaryDirectory(prefix="sassy_sandbox_") as td:
        td_path = Path(td)

        # (a) ordinary file -> staged
        victim = td_path / "victim.txt"
        victim.write_text("hello")
        out = await _safe_move_to_staging([str(victim)], "rm", f"rm {victim}")
        staged = td_path / _STAGING_FOLDER / "victim.txt"
        check("sandbox: victim.txt moved to _DELETE_/",
              staged.exists() and not victim.exists(),
              f"staged={staged.exists()} victim={victim.exists()} out={out!r}")

        # (b) collision handling
        second = td_path / "victim.txt"
        second.write_text("v2")
        await _safe_move_to_staging([str(second)], "rm", f"rm {second}")
        collided = td_path / _STAGING_FOLDER / "victim_1.txt"
        check("sandbox: collision -> victim_1.txt", collided.exists())

        # (c) protected path refused — sassymcp module file
        protected = Path(r"V:\Projects\SassyMCP\sassymcp\modules\shell.py")
        out = await _safe_move_to_staging([str(protected)], "rm", f"rm {protected}")
        check("sandbox: protected source refused",
              "REFUSED" in out and protected.exists(),
              f"out={out[:200]}")

        # (d) staging folder itself refused
        out = await _safe_move_to_staging([str(td_path / "_DELETE_")], "rm", "rm _DELETE_")
        check("sandbox: _DELETE_ folder refused", "REFUSED" in out, f"out={out[:200]}")

        # (e) missing target reported, not crashed
        out = await _safe_move_to_staging([str(td_path / "nope.txt")], "rm", "rm nope.txt")
        check("sandbox: missing target graceful", "not found" in out, f"out={out[:200]}")

asyncio.run(test_staging())


# ── sassy_safe_delete / sassy_write_file / sassy_move guards ────────
print("\n[5] fileops guards — sandbox")

# Reach into register() to get the tools. Easiest path: construct a fake
# server that collects them.
class _FakeServer:
    def __init__(self):
        self.tools = {}
    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco

import inspect as _inspect


def _aw(fn):
    """Adapt a captured tool so `await fn(...)` works whether the tool is
    declared `def` or `async def`. fileops tools are now plain `def` (the
    server's audit wrapper offloads them to a thread); the raw functions
    captured here aren't wrapped, so awaiting them directly would fail.
    """
    if _inspect.iscoroutinefunction(fn):
        return fn

    async def _wrapped(*a, **k):
        return fn(*a, **k)

    return _wrapped


from sassymcp.modules import fileops as fo
_fake = _FakeServer()
fo.register(_fake)
sassy_safe_delete = _aw(_fake.tools["sassy_safe_delete"])
sassy_write_file  = _aw(_fake.tools["sassy_write_file"])
sassy_move        = _aw(_fake.tools["sassy_move"])
sassy_copy        = _aw(_fake.tools["sassy_copy"])

async def test_fileops():
    with tempfile.TemporaryDirectory(prefix="sassy_fo_") as td:
        td_path = Path(td)

        # sassy_safe_delete: normal file
        f = td_path / "a.txt"
        f.write_text("x")
        msg = await sassy_safe_delete(str(f))
        check("safe_delete: moved a.txt", "Moved to staging" in msg, f"msg={msg}")

        # sassy_safe_delete: refuses _DELETE_ folder
        msg = await sassy_safe_delete(str(td_path / "_DELETE_"))
        check("safe_delete: refuses staging folder", "Refused" in msg, f"msg={msg}")

        # sassy_safe_delete: refuses protected (sassymcp source)
        msg = await sassy_safe_delete(r"V:\Projects\SassyMCP\sassymcp\modules\shell.py")
        check("safe_delete: refuses protected source", "Refused" in msg, f"msg={msg}")

        # sassy_write_file: rewrite snapshots existing content
        target = td_path / "doc.txt"
        target.write_text("original")
        msg = await sassy_write_file(str(target), "new contents", "rewrite")
        check("write_file: rewrite succeeded", "Written" in msg, f"msg={msg}")
        snaps = list((td_path / "_DELETE_").glob("doc.overwrite.*.txt"))
        check("write_file: snapshot created", len(snaps) == 1 and snaps[0].read_text() == "original",
              f"snaps={snaps}")

        # sassy_write_file: refuses protected source
        msg = await sassy_write_file(r"V:\Projects\SassyMCP\sassymcp\modules\shell.py", "junk", "rewrite")
        check("write_file: refuses protected", "Refused" in msg, f"msg={msg}")

        # sassy_move: refuses overwrite of existing destination
        a = td_path / "src.txt"; a.write_text("a")
        b = td_path / "dst.txt"; b.write_text("b")
        msg = await sassy_move(str(a), str(b))
        check("move: refuses existing destination", "already exists" in msg, f"msg={msg}")
        check("move: src not touched on refusal", a.exists())
        check("move: dst not touched on refusal", b.read_text() == "b")

        # sassy_move: refuses protected source
        msg = await sassy_move(r"V:\Projects\SassyMCP\sassymcp\modules\shell.py", str(td_path / "out.py"))
        check("move: refuses protected source", "Refused" in msg, f"msg={msg}")

        # v1.1.2: sassy_copy refuses existing destination (no silent overwrite)
        c1 = td_path / "c1.txt"; c1.write_text("one")
        c2 = td_path / "c2.txt"; c2.write_text("two")
        msg = await sassy_copy(str(c1), str(c2))
        check("copy: refuses existing destination", "already exists" in msg, f"msg={msg}")
        check("copy: dst unchanged on refusal", c2.read_text() == "two")

        # v1.1.2: sassy_copy refuses protected source
        msg = await sassy_copy(r"V:\Projects\SassyMCP\sassymcp\modules\shell.py", str(td_path / "stolen.py"))
        check("copy: refuses protected source", "Refused" in msg, f"msg={msg}")

        # v1.1.2: sassy_copy refuses protected destination
        msg = await sassy_copy(str(c1), r"V:\Projects\SassyMCP\sassymcp\modules\clobber.py")
        check("copy: refuses protected dest", "Refused" in msg, f"msg={msg}")

        # v1.1.2: sassy_copy normal happy path still works
        c3 = td_path / "c3.txt"
        msg = await sassy_copy(str(c1), str(c3))
        check("copy: normal happy path", "Copied" in msg and c3.read_text() == "one", f"msg={msg}")

asyncio.run(test_fileops())


# ── editor.py guards — v1.1.2 ────────────────────────────────────────
print("\n[5b] editor.py guards — sandbox")
from sassymcp.modules import editor as ed
_fake = _FakeServer()
ed.register(_fake)
sassy_edit_block = _aw(_fake.tools["sassy_edit_block"])
sassy_edit_multi = _aw(_fake.tools["sassy_edit_multi"])

async def test_editor():
    with tempfile.TemporaryDirectory(prefix="sassy_ed_") as td:
        td_path = Path(td)

        # edit_block: refuses protected file — using a real file path that
        # is_protected_path will flag WITHOUT the tool actually touching it.
        # We use a path that exists and is inside the protected tree.
        protected_target = r"V:\Projects\SassyMCP\sassymcp\modules\shell.py"
        msg = await sassy_edit_block(protected_target,
                                     "def _THIS_WILL_NEVER_MATCH_ANYTHING_xyzzy",
                                     "def NUKED")
        # The fix must refuse BEFORE attempting the edit, so the match text
        # doesn't matter — a refusal is what we want.
        check("edit_block: refuses protected file",
              "Refused" in msg or "refused" in msg.lower() or "protected" in msg.lower(),
              f"msg={msg[:200]}")

        # edit_block: happy path snapshots previous content
        target = td_path / "code.txt"
        target.write_text("alpha beta gamma")
        msg = await sassy_edit_block(str(target), "beta", "BETA")
        check("edit_block: happy path applies edit", "Edit applied" in msg, f"msg={msg[:200]}")
        check("edit_block: file content updated", target.read_text() == "alpha BETA gamma")
        snaps = list((td_path / "_DELETE_").glob("code.pre-edit.*.txt"))
        check("edit_block: snapshot created", len(snaps) == 1, f"snaps={snaps}")

        # edit_multi: refuses protected file
        msg = await sassy_edit_multi(
            r"V:\Projects\SassyMCP\sassymcp\modules\_security.py",
            '[{"old":"_THIS_WILL_NEVER_MATCH_ANYTHING_xyzzy","new":"NUKED"}]',
        )
        check("edit_multi: refuses protected file",
              "Refused" in msg or "refused" in msg.lower() or "protected" in msg.lower(),
              f"msg={msg[:200]}")

        # edit_multi: happy path snapshots
        m_target = td_path / "multi.txt"
        m_target.write_text("one two three")
        import json as _j
        msg = await sassy_edit_multi(str(m_target), _j.dumps([
            {"old": "one", "new": "ONE"},
            {"old": "three", "new": "THREE"},
        ]))
        check("edit_multi: happy path applies", "Applied" in msg)
        check("edit_multi: content updated", m_target.read_text() == "ONE two THREE")
        snaps = list((td_path / "_DELETE_").glob("multi.pre-edit.*.txt"))
        check("edit_multi: snapshot created", len(snaps) == 1, f"snaps={snaps}")

asyncio.run(test_editor())


# ── session.py gating ────────────────────────────────────────────────
print("\n[6] session.py send/start gating")
from sassymcp.modules import session as sess_mod
_fake = _FakeServer()
sess_mod.register(_fake)
sassy_session_start = _fake.tools["sassy_session_start"]
sassy_session_send  = _fake.tools["sassy_session_send"]
sassy_session_stop  = _fake.tools["sassy_session_stop"]

async def test_session():
    import json as _json
    # Start a real shell so send() has a target.
    r = await sassy_session_start("sbx", "powershell", "")
    check("session: started", "started" in r, f"r={r}")

    # Direct delete via send — should be refused.
    r = await sassy_session_send("sbx", "del foo.txt")
    blocked = "Delete command blocked" in r
    check("session_send: direct del blocked", blocked, f"r={r}")

    # Alias ri — should be refused.
    r = await sassy_session_send("sbx", "ri foo")
    check("session_send: ri alias blocked", "Delete command blocked" in r, f"r={r}")

    # Wrapper via cmd /c — should be refused.
    r = await sassy_session_send("sbx", "cmd /c del foo")
    check("session_send: cmd /c wrapper blocked", "Delete command blocked" in r, f"r={r}")

    # Non-destructive — should be allowed.
    r = await sassy_session_send("sbx", "echo sandbox-ok")
    check("session_send: echo allowed", "sent" in r, f"r={r}")

    # start() with delete command — refused before the shell even spawns.
    r = await sassy_session_start("sbx2", "powershell", "del foo")
    check("session_start: initial del blocked", "blocked" in r.lower(), f"r={r}")

    await sassy_session_stop("sbx")

asyncio.run(test_session())


# ── linux.py gating ──────────────────────────────────────────────────
print("\n[7] linux.py gating")
from sassymcp.modules import linux as linux_mod
_fake = _FakeServer()
linux_mod.register(_fake)
sassy_linux_exec = _fake.tools["sassy_linux_exec"]

async def test_linux():
    r = await sassy_linux_exec("rm foo.txt", 5)
    check("linux_exec: rm blocked", "blocked by interceptor" in r, f"r={r[:200]}")
    r = await sassy_linux_exec("cmd /c del foo", 5)
    check("linux_exec: wrapper blocked", "blocked by interceptor" in r, f"r={r[:200]}")

asyncio.run(test_linux())


# ── audit_clear rotation ─────────────────────────────────────────────
print("\n[8] audit_clear — rotation not unlink")
from sassymcp.modules import audit as audit_mod
_fake = _FakeServer()
audit_mod.register(_fake)
sassy_audit_clear = _aw(_fake.tools["sassy_audit_clear"])

async def test_audit():
    # Without confirm it must refuse.
    r = await sassy_audit_clear("")
    check("audit_clear: refuses without confirm", "Refused" in r, f"r={r}")

asyncio.run(test_audit())


# ── selfmod removed — register is a no-op ─────────────────────────────
print("\n[9] selfmod — permanently removed")
from sassymcp.modules import selfmod as selfmod_mod
from sassymcp.modules._tool_loader import TOOL_GROUPS as _TG
_fake = _FakeServer()
selfmod_mod.register(_fake)
check("selfmod group absent from TOOL_GROUPS", "selfmod" not in _TG)
check("selfmod register adds no tools",
      not any(n.startswith("sassy_selfmod_") for n in _fake.tools),
      f"tools={list(_fake.tools)}")


# ── adb_shell detect_delete_intent — v1.1.2 ──────────────────────────
print("\n[10] adb_shell — destructive gate")
# adb_shell lives in sassymcp.modules.adb; we only exercise the validation
# path (not the real adb invocation). The function runs _run_adb on success;
# on block it should short-circuit with an error string.
from sassymcp.modules import adb as adb_mod
_fake = _FakeServer()
adb_mod.register(_fake)
sassy_adb_shell = _fake.tools["sassy_adb_shell"]

async def test_adb():
    # Use a fake device ID so the call NEVER reaches a real phone — adb
    # will error with "device 'zzz_fake_test' not found" before executing.
    FAKE = "zzz_fake_test_device"

    # Use a harmless path that would be no-op even if it somehow got
    # through: /tmp/sassymcp-intercept-test-nonexistent
    harmless = "rm /tmp/sassymcp_intercept_test_nonexistent_file_xyzzy"

    r = await sassy_adb_shell(harmless, device=FAKE)
    check("adb_shell: rm blocked without override",
          "blocked" in r.lower() or "destructive" in r.lower(),
          f"r={r[:200]}")

    r = await sassy_adb_shell(harmless, device=FAKE, allow_destructive=True)
    # With override, our gate is passed. adb itself errors on the fake
    # device, but that's not our gate blocking it.
    check("adb_shell: rm allowed with override",
          "blocked" not in r.lower() and "destructive" not in r.lower(),
          f"r={r[:200]}")

asyncio.run(test_adb())


# ── v1.3.0: quoted-string pre-strip & allow_pattern ──────────────────
print("\n[11] _strip_quoted_strings — quoted contents neutralized")
from sassymcp.modules._security import _strip_quoted_strings

strip_cases = [
    # (raw, expected — chars inside quotes replaced with x, quote chars kept)
    ('echo "hello > world"',       'echo "xxxxxxxxxxxxx"'),
    ("echo 'a > b'",               "echo 'xxxxx'"),
    ('plain > target',             'plain > target'),                # no quotes — untouched
    ('one "ab" two "cd"',          'one "xx" two "xx"'),
    ('a "with > inside" b',        'a "xxxxxxxxxxxxx" b'),
    ("`backticks > here`",         "`xxxxxxxxxxxxxxxx`"),
]
for raw, expected in strip_cases:
    got = _strip_quoted_strings(raw)
    check(f"strip {raw!r} -> {expected!r}", got == expected, f"got={got!r}")


print("\n[12] detect_delete_intent — quoted `>` no longer flags redirect")
quoted_cases = [
    # The classic v1.2.0 false positive: `>` lives inside a parameter value.
    ('Start-Process -RedirectStandardOutput "V:\\logs\\bridge.out.log"', False),
    # Even a literal `>` character inside a quoted string is data, not a redirect.
    # Use a non-/tmp, non-exempt-extension target (the .log/.txt/.csv/.out/
    # .err/.json redirect exemptions would otherwise suppress the match).
    ('echo "a > b" > C:\\Users\\me\\out.dat', True),   # the SECOND `>` is real
    ('echo "a > b"',                False),  # only the quoted `>` exists
    ("echo 'a > b'",                False),
    # Bare redirect must still trip (non-exempt ext; .txt/.log are exempt).
    ('command > target.dll',        True),
    # Redirect inside backticks (PS subexpression syntax) still data.
    ('Write-Host `> data`',         False),
]
for cmd, expected in quoted_cases:
    got, kw = detect_delete_intent(cmd)
    check(f"q-strip detect={expected} <- {cmd!r}", got == expected, f"got=({got},{kw!r})")


print("\n[13] sassy_shell — allow_pattern opt-in bypass")
# Reach into the registered tool and exercise it against a sandbox dir.
from sassymcp.modules import shell as shell_mod
_fake = _FakeServer()
shell_mod.register(_fake)
sassy_shell = _fake.tools["sassy_shell"]

async def test_allow_pattern():
    with tempfile.TemporaryDirectory(prefix="sassy_ap_") as td:
        td_path = Path(td)

        # truncate-by-redirect is now LOW tier — runs by default after a log entry.
        target = (td_path / "out.log").as_posix()
        cmd_redirect = f'echo hi > {target}'
        r = await sassy_shell(cmd_redirect, "powershell", 10)
        check("allow_pattern: low-tier redirect runs by default",
              "[exit:" in r and "blocked" not in r.lower(),
              f"r={r[:200]}")
        check("allow_pattern: low-tier file IS created (no block)",
              Path(target).exists())

        # MEDIUM-tier pattern (out-file -force) is blocked by default.
        target_m = (td_path / "med.log").as_posix()
        cmd_med = f'"hi" | Out-File -Force {target_m}'
        r = await sassy_shell(cmd_med, "powershell", 10)
        check("allow_pattern: medium-tier blocked by default",
              "blocked" in r.lower() and "out-file -force" in r,
              f"r={r[:200]}")
        check("allow_pattern: medium-tier file NOT created when blocked",
              not Path(target_m).exists())

        # MEDIUM-tier with matching allow_pattern -> bypassed and runs.
        r = await sassy_shell(cmd_med, "powershell", 10, allow_pattern="out-file -force")
        check("allow_pattern: medium-tier bypass executes",
              "[exit:" in r,
              f"r={r[:200]}")
        check("allow_pattern: medium-tier file WAS created via bypass",
              Path(target_m).exists())

        # Wrong allow_pattern label on a MEDIUM hit still blocks.
        target_m2 = (td_path / "med2.log").as_posix()
        cmd_med2 = f'"hi" | Out-File -Force {target_m2}'
        r = await sassy_shell(cmd_med2, "powershell", 10, allow_pattern="copy /y")
        check("allow_pattern: wrong label still blocks medium",
              "blocked" in r.lower(),
              f"r={r[:200]}")
        check("allow_pattern: wrong-label file NOT created",
              not Path(target_m2).exists())

        # Wildcard '*' is no longer accepted — it used to bypass every safety
        # regex in one call (a foot-gun). It must be refused, and the file
        # must NOT be created. Callers name the exact pattern label instead.
        target3 = (td_path / "out3.log").as_posix()
        cmd3 = f'"hi" | Out-File -Force {target3}'
        r = await sassy_shell(cmd3, "powershell", 10, allow_pattern="*")
        check("allow_pattern: wildcard refused (no longer accepted)",
              "no longer accepted" in r.lower(),
              f"r={r[:200]}")
        check("allow_pattern: wildcard file NOT created",
              not Path(target3).exists())

        # KEYWORD matches must NEVER be bypassable via allow_pattern —
        # rm/del/ri only ever go through the staging mover.
        f = td_path / "stillsafe.txt"
        f.write_text("x")
        r = await sassy_shell(f"del {f}", "powershell", 10, allow_pattern="*")
        check("allow_pattern: keyword (del) NOT bypassable with '*'",
              "Delete command blocked" in r,
              f"r={r[:200]}")
        # Keyword path stages the target, doesn't run del.
        check("allow_pattern: keyword target staged, not deleted",
              not f.exists() and (td_path / "_DELETE_" / "stillsafe.txt").exists())

asyncio.run(test_allow_pattern())


print("\n[14] sassy_audit_false_positives — surfaces pattern events")
import json as _json
from sassymcp.modules import audit as audit_mod
_fake = _FakeServer()
audit_mod.register(_fake)
sassy_audit_false_positives = _aw(_fake.tools["sassy_audit_false_positives"])

async def test_audit_fp():
    # Run two MEDIUM-tier commands so audit.jsonl picks up a fresh
    # block + bypass pair (low-tier wouldn't generate a pattern_block).
    with tempfile.TemporaryDirectory(prefix="sassy_fp_") as td:
        target = (Path(td) / "x.log").as_posix()
        await sassy_shell(f'"a" | Out-File -Force {target}', "powershell", 10)             # block
        await sassy_shell(f'"b" | Out-File -Force {target}', "powershell", 10,
                          allow_pattern="out-file -force")                                  # bypass

    out = await sassy_audit_false_positives(count=10, include_bypasses=True)
    check("audit_false_positives: shows pattern_block",
          "pattern_block" in out, f"out={out[:300]}")
    check("audit_false_positives: shows pattern_bypass",
          "pattern_bypass" in out, f"out={out[:300]}")

    out = await sassy_audit_false_positives(count=10, include_bypasses=False)
    check("audit_false_positives: hides bypasses when asked",
          "pattern_bypass" not in out and "pattern_block" in out,
          f"out={out[:300]}")

asyncio.run(test_audit_fp())


# ── v1.4.0: validate_command_tiered — string-literal allow path ─────
print("\n[15] validate_command_tiered — block-list words inside string literals")
from sassymcp.modules._security import validate_command, validate_command_tiered

tiered_cases = [
    # (cmd, expected_ok, expected_tier, hint)
    # Real execution of a hardcoded block — high tier, blocked.
    ("format c:",                                      False, "high"),
    ("diskpart /s script.txt",                         False, "high"),
    # Block-list word inside a string literal — low tier (allowed by default).
    ('echo "format the disk later"',                    False, "low"),
    ("Set-Content readme.txt 'see also: diskpart'",     False, "low"),
    # Regression: PowerShell verb-Noun and -Flag forms must NOT trip the
    # bare 'format'/'shutdown'/'reboot' word blocks. A stale build that did
    # naive substring matching blocked `Get-Date -Format ...`, `Format-Table`,
    # etc. everywhere — effectively killing shell access. The word-boundary
    # matcher (_WORD_MATCH_BLOCKS) must let these through while still blocking
    # the bare destructive verbs (`format c:`, `shutdown /s`) above.
    ("Get-Date -Format HHmmss",                        True,  ""),
    ("Get-Process | Format-Table",                     True,  ""),
    ("Get-Service | Format-List",                      True,  ""),
    # Clean commands.
    ("Get-ChildItem foo",                              True,  ""),
    ("echo hello",                                     True,  ""),
]
for cmd, exp_ok, exp_tier in tiered_cases:
    ok, tier, _err = validate_command_tiered(cmd)
    check(
        f"tiered ok={exp_ok}/tier={exp_tier!r} <- {cmd!r}",
        ok == exp_ok and tier == exp_tier,
        f"got=({ok},{tier!r})",
    )

# Backwards-compat: validate_command still returns (ok, err) and now passes
# string-literal hits because they're low-tier.
ok, _err = validate_command('echo "format the disk later"')
check("validate_command: string-literal hit treated as low (returns False)", ok is False)


print("\n[16] sassy_shell — string-literal hardcoded block runs by default")
async def test_blocklist_literal():
    with tempfile.TemporaryDirectory(prefix="sassy_lit_") as td:
        target = (Path(td) / "note.txt").as_posix()
        # 'format' inside a string literal — used to be a hard block.
        # Now classified as low-tier and falls through to execution.
        cmd = f'"see format step later" | Out-File {target}'
        r = await sassy_shell(cmd, "powershell", 10)
        check("blocklist-literal: runs by default (Out-File without -Force)",
              "[exit:" in r and "blocked" not in r.lower(),
              f"r={r[:200]}")
        check("blocklist-literal: file written",
              Path(target).exists())

asyncio.run(test_blocklist_literal())


# ── v1.4.0: confirm-token round-trip ────────────────────────────────
print("\n[17] sassy_shell + sassy_shell_confirm — confirm flow")
sassy_shell_confirm = _fake.tools.get("sassy_shell_confirm")
# _fake from [14] only has audit tools registered. Re-grab from shell registration.
_fake_shell = _FakeServer()
shell_mod.register(_fake_shell)
sassy_shell = _fake_shell.tools["sassy_shell"]
sassy_shell_confirm = _fake_shell.tools["sassy_shell_confirm"]

from sassymcp.modules import runtime_config as _rc

async def test_confirm_flow():
    # Flip config to confirm mode.
    _rc.set_val("interceptor.destructiveAction", "confirm")
    try:
        with tempfile.TemporaryDirectory(prefix="sassy_cf_") as td:
            target = (Path(td) / "doc.txt").as_posix()
            cmd_med = f'"hello" | Out-File -Force {target}'

            # Medium-tier under confirm mode -> JSON token response.
            r = await sassy_shell(cmd_med, "powershell", 10)
            check("confirm: medium returns confirmation_required",
                  '"status": "confirmation_required"' in r and '"tier": "medium"' in r,
                  f"r={r[:300]}")
            check("confirm: file NOT created at issue time",
                  not Path(target).exists())

            entry = _json.loads(r)
            tok = entry["token"]

            # Replay the token -> executes.
            r = await sassy_shell_confirm(tok)
            check("confirm: redemption executes the command",
                  "[exit:" in r,
                  f"r={r[:200]}")
            check("confirm: file IS created after confirm",
                  Path(target).exists())

            # Token is single-use.
            r = await sassy_shell_confirm(tok)
            check("confirm: token is single-use",
                  "Error" in r and "not found" in r,
                  f"r={r[:200]}")

            # HIGH tier requires the typed phrase.
            target_h = (Path(td) / "h.log").as_posix()
            cmd_high = f'"hi" | Out-File -Overwrite {target_h}'  # medium, but use a high
            # Use a real HIGH pattern — robocopy /MIR.
            src = Path(td) / "src"; src.mkdir()
            dst = Path(td) / "dst"; dst.mkdir()
            (src / "a.txt").write_text("a")
            cmd_high = f"robocopy {src} {dst} /MIR"
            r = await sassy_shell(cmd_high, "powershell", 30)
            check("confirm: high returns confirmation_required + phrase",
                  '"tier": "high"' in r and '"phrase_required"' in r,
                  f"r={r[:300]}")
            entry = _json.loads(r)
            tok = entry["token"]
            phrase = entry["phrase_required"]

            # Wrong phrase -> reject, token still valid.
            r = await sassy_shell_confirm(tok, "wrong words")
            check("confirm: high rejects wrong phrase",
                  "phrase mismatch" in r,
                  f"r={r[:200]}")

            # Right phrase -> executes.
            r = await sassy_shell_confirm(tok, phrase)
            check("confirm: high executes with correct phrase",
                  "[exit:" in r,
                  f"r={r[:200]}")
    finally:
        _rc.set_val("interceptor.destructiveAction", "block")

asyncio.run(test_confirm_flow())


# ── v1.4.0: sassy_write_file — encoding + line_endings ───────────────
print("\n[18] sassy_write_file — encoding and line_endings")
from sassymcp.modules import fileops as fo
_fake = _FakeServer()
fo.register(_fake)
sassy_write_file = _aw(_fake.tools["sassy_write_file"])

async def test_write_file_options():
    with tempfile.TemporaryDirectory(prefix="sassy_wf_") as td:
        td_path = Path(td)

        # line_endings=lf normalizes mixed input to LF.
        p = td_path / "lf.txt"
        msg = await sassy_write_file(str(p), "a\r\nb\rc\n", "rewrite",
                                     "utf-8", "lf")
        check("write_file: lf normalization", "Written" in msg)
        check("write_file: lf bytes", p.read_bytes() == b"a\nb\nc\n",
              f"got={p.read_bytes()!r}")

        # line_endings=crlf normalizes everything to CRLF.
        p = td_path / "crlf.txt"
        msg = await sassy_write_file(str(p), "a\nb\nc", "rewrite",
                                     "utf-8", "crlf")
        check("write_file: crlf normalization", "Written" in msg)
        check("write_file: crlf bytes", p.read_bytes() == b"a\r\nb\r\nc",
              f"got={p.read_bytes()!r}")

        # encoding=utf-16 produces a UTF-16 file.
        p = td_path / "u16.txt"
        msg = await sassy_write_file(str(p), "hello", "rewrite", "utf-16")
        data = p.read_bytes()
        check("write_file: utf-16 encoding (BOM)",
              data[:2] in (b"\xff\xfe", b"\xfe\xff"),
              f"got={data[:8]!r}")

        # Bad encoding errors clearly.
        p = td_path / "bad.txt"
        msg = await sassy_write_file(str(p), "x", "rewrite", "not-a-codec")
        check("write_file: unknown encoding errors", "Error" in msg, f"msg={msg}")

        # Bad line_endings errors clearly.
        p = td_path / "bad2.txt"
        msg = await sassy_write_file(str(p), "x", "rewrite", "utf-8", "weird")
        check("write_file: unknown line_endings errors", "Error" in msg, f"msg={msg}")

        # Content can include destructive-looking words — write_file does
        # NOT scan content, only the path.
        p = td_path / "script.ps1"
        risky = "# Note: do not run 'format c:' or 'rm -rf /'\nWrite-Host done"
        msg = await sassy_write_file(str(p), risky, "rewrite")
        check("write_file: destructive words in content are fine",
              "Written" in msg and p.read_text(encoding="utf-8") == risky)

asyncio.run(test_write_file_options())


print("\n==================")
print(f"{PASS} passed, {FAIL} failed")
print("==================")
sys.exit(0 if FAIL == 0 else 1)
