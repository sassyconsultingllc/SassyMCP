## vX.Y.Z — <one-line summary>

<!--
This is the template every release follows. Copy this file to
docs/releases/vX.Y.Z.md and fill in the sections. scripts/verify-release.ps1
parses the `## Verification` fenced block below to know what to run.

Sections marked REQUIRED are load-bearing — the CI and verify script
depend on them existing. Optional sections can be dropped if irrelevant.
-->

### What changed                                <!-- REQUIRED -->

<Short paragraph: the user-visible behaviour change. Lead with the
behaviour, not the internal mechanism.>

### The bug / motivation                         <!-- optional, for fixes -->

<What was broken or limiting before, and why it mattered. Include a
concrete reproduction if you can.>

### The fix / approach                           <!-- REQUIRED -->

<How the change works mechanically. Files + functions + the key
decision (and any constants/thresholds the reader will want to find).>

### Test coverage                                <!-- REQUIRED -->

<Bulleted summary of what the new tests assert, plus any existing
suite re-runs that establish no regression.>

### Verification                                 <!-- REQUIRED -->

The fenced block below is executed by `scripts/verify-release.ps1`.
Keep each line independently runnable. Lines starting with `#` are
ignored (use them for narrative inside the fence).

```ps1
# Unit + regression tests for this release
python tests/test_<feature>.py
python tests/test_intercept.py
```

<For releases that need a runtime/live check, describe it in plain
prose below the fence — humans run those; the script only parses the
fence. Example: "Call sassy_X with Y and expect Z back. Failure mode:
W.">

### Compatibility                                <!-- REQUIRED -->

- <How existing callers/configs are affected. Be explicit about
  breaking changes — none, opt-in, or forced migration.>

### Files touched                                <!-- optional -->

- `path/to/file.py` — one-line summary of why this file changed.
