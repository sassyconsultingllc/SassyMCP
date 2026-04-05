

## Autonomous Continuation & Self-Completion Protocol

Claude cannot literally start new sessions. But with MadameClaude + SassyMCP Crosslink, Claude can make session boundaries nearly invisible. The goal: any new session picks up exactly where the last one left off with zero ramp-up.

### Core Infrastructure

| Tool | Role |
|------|------|
| **MadameClaude** (`madame_*`) | Persistent memory — task state, decisions, progress, blockers |
| **SassyMCP Crosslink** (`sassy_crosslink_*`) | Inter-session message queue — handoff payloads, continuation signals |
| **SassyMCP Files** (`sassy_write_file`) | State dumps — full context snapshots too large for memory entries |

### Session Startup Protocol

Every session, Claude MUST:

1. **Call `madame_context`** — loads all critical/high memories + recent milestones
2. **Call `sassy_crosslink_recv` on channel `task-handoff`** — check for continuation payloads from prior session
3. **If handoff exists**: resume immediately from the handoff state, no questions asked
4. **If no handoff**: check `madame_search` for the project SaS is likely working on (based on greeting/context clues), load relevant task state
5. **Never ask "what were we working on?"** — figure it out from MadameClaude + Crosslink

### Session Shutdown / Handoff Protocol

When context is getting long (~80% consumed), OR when a natural task boundary is reached, OR when SaS says "continue this later":

1. **Write a handoff payload** via `sassy_crosslink_send` to channel `task-handoff`:
```json
{
  "task": "refactor sassy-browser TLS module",
  "status": "in-progress",
  "completed": ["fixed rustls-native-certs", "wired MCP infrastructure"],
  "next_steps": ["connect Claude API to browser MCP", "test TLS with BitDefender"],
  "blockers": [],
  "files_touched": ["src/net/tls.rs", "src/mcp/client.rs"],
  "context_notes": "BitDefender HTTPS inspection CA not in Mozilla bundle - use native certs"
}
```
2. **Update MadameClaude** with `madame_remember` — key pattern: `task_<taskname>_state`
3. **Log milestone** via `madame_log` with event_type `milestone`
4. **Tell SaS**: "Handoff written. Next session will pick up at [specific point]."

### MadameClaude Memory Organization — Task-Based, Not Project-Based

**Critical principle:** Organize memories by TASK CONCEPT, not by project name. The same type of task (e.g., "TLS configuration", "security hardening", "build system fixes") appears across multiple projects. Group them so Claude can transfer knowledge.

#### Key Naming Convention

```
task_<concept>_<project>_state     → Current state of a specific task
pattern_<concept>                  → Reusable pattern/solution learned
blocker_<concept>_<project>        → Known blocker, stays until resolved
decision_<concept>                 → Architectural decision (cross-project)
```

#### Examples

```
task_tls_sassybrowser_state        → TLS work on Sassy Browser
task_tls_racrust_state             → TLS work on Riverview
pattern_tls_native_certs           → "Use rustls-native-certs for system CA stores" (applies everywhere)
pattern_cloudflare_worker_routing  → Host-based routing pattern (applies to any CF worker)
blocker_electron_freeze_guard      → Guard Electron UI freezing issue
decision_encryption_mandatory      → "All comms encrypted, TX refuses without auth" (SassyTalkie, reusable)
```

#### Tags for Cross-Cutting Concerns

Use tags that group by concept across projects:
- `tls`, `security`, `build`, `ci`, `transport`, `ui`, `api`, `deployment`
- `pattern` — reusable solution
- `blocker` — unresolved problem
- `task-active` — currently being worked on
- `task-complete` — done, keep for reference
- `handoff` — continuation state

This way, when Claude starts a TLS task on ANY project, `madame_search` for "tls" returns all TLS knowledge — patterns, past fixes, active blockers — regardless of which project they came from.

### Within-Session Self-Completion

**Never stop and ask "should I continue?" during a task.** Rules:

1. **If the task has more steps → keep going.** Don't pause between steps.
2. **If context is getting long → write handoff, tell SaS, keep working until you physically can't.**
3. **If you hit an error → fix it and continue. Don't stop to report unless it's a blocker.**
4. **If a subtask spawns → do it inline. Don't ask "should I also do X?" — just do X.**
5. **If SaS said "do all of it" or gave a list → complete the entire list. Don't stop at item 3 of 10.**

### Crosslink Channels

| Channel | Purpose |
|---------|---------|
| `task-handoff` | Session continuation payloads |
| `task-complete` | Completion notifications (SaS can poll from any device) |
| `blocker` | Unresolved issues that need human input |
| `context-dump` | Full state snapshots (read by next session on startup) |

### Cross-Project Pattern Propagation

When Claude solves a problem on one project, check if the pattern applies elsewhere:

1. Solve the problem on the current project
2. Store as `pattern_<concept>` in MadameClaude with tag `pattern`
3. Check `madame_search` for other projects with similar code/architecture
4. If applicable: mention to SaS "This fix also applies to [other project] — want me to apply it there too?"
5. If SaS says yes (or context makes it obvious): apply immediately, don't ask again

### State File Snapshots (Large Context)

For tasks too complex for a MadameClaude entry (>500 chars of state), write a state file:

```
V:\Projects\<project>\CLAUDE_STATE.json
```

Structure:
```json
{
  "last_session": "session_XXXXX",
  "timestamp": "2026-03-22T02:00:00Z",
  "task": "description",
  "progress": {
    "completed": [],
    "in_progress": [],
    "remaining": []
  },
  "decisions": [],
  "files_modified": [],
  "notes": ""
}
```

Claude reads this at session start if working on that project. Updates it before handoff.

### The Goal

SaS opens a new session. Claude immediately knows:
- What was being worked on
- What's done, what's left
- What patterns were learned
- What blockers exist
- What the next step is

And starts executing. No warmup. No "what would you like to work on?" Zero friction.
