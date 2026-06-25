"""SassyMCP MCP Prompts — slash-menu shortcuts that most MCP clients expose.

The MCP protocol's `prompts` slot lets a server register named prompts
that clients can invoke from a slash menu (Claude Desktop, Cursor's MCP
integration, etc.). When the user picks one, the client substitutes the
prompt's text into the conversation as a fresh user turn — no natural-
language interpretation needed.

These prompts encode SassyMCP's "expected workflow" in a form the user
can trigger with one click. Each prompt accepts arguments and produces
a structured request the model can execute mechanically.

The handler functions return a list of mcp.Prompt content blocks — for
fastmcp >= 1.0 a single string return is also accepted and wrapped.
"""

import logging

logger = logging.getLogger("sassymcp.prompts")


def register(server):
    """Register MCP prompts via fastmcp's @server.prompt() decorator."""

    @server.prompt(
        name="pr-review",
        description=(
            "Run a SassyMCP PR review on a GitHub pull request. Calls the "
            "combo tool to fetch metadata + diff + comments + check runs in "
            "one shot, then summarises the change, flags concerns, and "
            "states a merge recommendation."
        ),
    )
    def pr_review(owner: str, repo: str, pr: str) -> str:
        """Slash-shortcut: /sassymcp:pr-review owner=foo repo=bar pr=42"""
        return (
            f"Use sassy_combo_pr_review with owner='{owner}', repo='{repo}', "
            f"pr={pr} to fetch the PR's metadata, truncated diff, top comments, "
            f"and CI check status in a single call. Then produce a review with:\n\n"
            f"1. **One-line summary** of what the PR does\n"
            f"2. **Concerns** (if any) — security, breaking changes, missing tests, "
            f"perf hot paths. Be specific with file:line references.\n"
            f"3. **CI status** — note any failing checks and whether they're transient\n"
            f"4. **Merge recommendation** — approve / request changes / block, with reasoning\n\n"
            f"If the diff was truncated, ask whether to fetch the full diff for specific files."
        )

    @server.prompt(
        name="phone-status",
        description=(
            "Snapshot the current state of the connected Android device — "
            "foreground app, battery, WiFi, notifications, low-res screenshot, "
            "and parsed UI accessibility tree — in one combo call."
        ),
    )
    def phone_status(device: str = "") -> str:
        """Slash-shortcut: /sassymcp:phone-status"""
        device_arg = f"device='{device}'" if device else "device=''"
        return (
            f"Use sassy_combo_phone_observe with {device_arg} (and "
            f"include_glance=True for a screenshot). Report:\n\n"
            f"- What's the foreground app\n"
            f"- Battery level + WiFi state\n"
            f"- Notification count and any sensitive contexts (login, payment, "
            f"2FA — those auto-block tap/swipe)\n"
            f"- A short list of the 5-10 most-actionable UI elements visible "
            f"(buttons, text inputs) with their on-screen coordinates so "
            f"follow-up taps can target them precisely\n\n"
            f"Don't tap anything yet — this is observation only. Wait for the "
            f"user's next instruction."
        )

    @server.prompt(
        name="resume",
        description=(
            "Pick up where the prior session left off. Loads MadameClaude "
            "memory context AND any cross-platform task-handoff messages, "
            "then executes the next_steps from the handoff immediately."
        ),
    )
    def resume() -> str:
        """Slash-shortcut: /sassymcp:resume"""
        return (
            "Resume the prior session. Do this in order WITHOUT asking the "
            "user any 'what were we working on' questions:\n\n"
            "1. `sassy_memory_context` — pulls active tasks, handoffs, recent "
            "patterns, and milestones into context.\n"
            "2. `sassy_crosslink_recv channel='task-handoff' unread_only=True "
            "limit=5` — pulls cross-client handoff signals.\n"
            "3. If a handoff exists with a 'next_steps' field, execute those "
            "steps NOW. The handoff IS the answer.\n"
            "4. If no handoff, infer from active tasks in memory_context what "
            "to work on. State your inferred plan in one sentence and start.\n\n"
            "Discipline: never say 'what were we working on?' — the memory "
            "system tells you. Use it."
        )

    @server.prompt(
        name="codebase-grep",
        description=(
            "Search the codebase for a pattern with surrounding context — "
            "ranked top-5 files with 5 lines of context around each hit. "
            "Replaces the search-then-read-N-files dance for 'where is X used'."
        ),
    )
    def codebase_grep(pattern: str, path: str = ".") -> str:
        """Slash-shortcut: /sassymcp:codebase-grep pattern=foo"""
        return (
            f"Use sassy_combo_codebase_grep with pattern='{pattern}' "
            f"and path='{path}'. Report the top files with line numbers and "
            f"surrounding context. If the pattern is widely used (>5 files), "
            f"summarise where it tends to appear (e.g., 'mostly in tests/' or "
            f"'spread across modules/'). If it's <=5 files, walk through each "
            f"hit briefly so the user can navigate."
        )

    @server.prompt(
        name="brain-status",
        description=(
            "Report SassyMCP's current state — license tier, loaded tool "
            "groups, context window cost, top tools by usage score, recent "
            "audit activity, and pruning candidates."
        ),
    )
    def brain_status() -> str:
        """Slash-shortcut: /sassymcp:brain-status"""
        return (
            "Run all of these in parallel (asyncio.gather equivalent — issue "
            "the tool calls back-to-back without waiting between them):\n\n"
            "- `sassy_setup_status` — license tier and what's configured\n"
            "- `sassy_tool_groups` — which groups are loaded\n"
            "- `sassy_context_estimate` — context window cost in tokens\n"
            "- `sassy_observability_tool_stats` — top-10 by usage score plus "
            "pruning suggestions\n"
            "- `sassy_recent_tool_calls max_results=20` — last 20 invocations\n\n"
            "Then summarise: what's loaded, what's hot (recent activity), what's "
            "cold (pruning suggestions). Flag anything notable — license expired, "
            "context cost climbing, errors in recent calls."
        )

    @server.prompt(
        name="setup-sassy",
        description=(
            "Walk the first-run setup wizard for SassyMCP — persona, GitHub "
            "token, optional Linux/Android, optional Pro license activation."
        ),
    )
    def setup_sassy() -> str:
        """Slash-shortcut: /sassymcp:setup-sassy"""
        return (
            "Walk the SassyMCP first-run setup. The onboarding hook should "
            "auto-activate, but if not, call `sassy_hooks_activate "
            "name='onboarding'` first. Then:\n\n"
            "1. `sassy_setup_license action='status'` — current tier\n"
            "2. `sassy_setup_wizard` — ask the user about role, expertise, "
            "languages, frameworks, communication style. Build persona.md.\n"
            "3. `sassy_setup_tools action='check'` — install required tools "
            "(tesseract for OCR is mandatory; adb/scrcpy if has_android; "
            "plink if has_linux).\n"
            "4. `sassy_setup_github action='check'` — token configured? If "
            "not, `action='open_browser'` then `action='save_token'`.\n"
            "5. `sassy_setup_ssh` if user has Linux/WSL/remote.\n"
            "6. `sassy_setup_status` — confirm all green.\n\n"
            "Be patient on first-time users; fast and confirmatory on "
            "returning users."
        )

    @server.prompt(
        name="discover",
        description=(
            "Orient in the SassyMCP toolset before acting: confirm the server "
            "is whole and which runtime it is, list what it can actually do, "
            "and match the task to a domain playbook. Run at the start of any "
            "task on an unfamiliar or just-connected server."
        ),
    )
    def discover(task: str = "") -> str:
        """Slash-shortcut: /sassymcp:discover task='audit my site'"""
        step4 = (
            f"4. `sassy_hooks_suggest user_text='{task}'` — if a domain "
            f"playbook matches, `sassy_hooks_activate` it and follow it.\n"
            if task else
            "4. Once you know the task, `sassy_hooks_suggest "
            "user_text='<the request>'` — activate any matching playbook and "
            "follow it.\n"
        )
        return (
            "Orient before acting. Run these in order:\n\n"
            "1. `sassy_self_check` — am I whole? Note `runtime` (source vs "
            "frozen), `version`, and any `broken` modules. If anything is "
            "BROKEN, surface it before proceeding.\n"
            "2. `sassy_tool_catalog` — the live name->purpose->group map of "
            "every registered tool. Ground truth for what's available; don't "
            "infer capability from by-name guesses (lazy-loading hides "
            "on-demand tools).\n"
            "3. If a needed tool seems absent, it's likely a dormant on-demand "
            "group — `sassy_tool_groups` shows them, `sassy_tool_group_toggle` "
            "loads one.\n"
            f"{step4}"
            "\nThen state in one line what the server offers that's relevant to "
            "the task, and proceed."
        )

    logger.info("prompts: 7 MCP prompts registered (pr-review, phone-status, "
                "resume, codebase-grep, brain-status, setup-sassy, discover)")
