"""Editor - Surgical diff-based file editing.

Provides edit_block functionality: find exact text in a file and replace it
without rewriting the entire file. Includes fuzzy matching fallback with
character-level diff reporting when exact match fails.

v1.1.2: refuses edits on protected paths (SassyMCP source, ~/.sassymcp) and
snapshots existing content to _DELETE_/<name>.pre-edit.<ts><ext> before
applying so destructive edits can always be undone.
"""

import difflib
import shutil
import time
from pathlib import Path

from sassymcp.modules._security import is_protected_path
from sassymcp.modules import audit as _audit

_STAGING_FOLDER = "_DELETE_"


def _snapshot_before_edit(p: Path) -> tuple[bool, str]:
    """Copy p into its adjacent _DELETE_/ as <stem>.pre-edit.<ts><suffix>.

    Returns (ok, path_or_error). Failure is returned so the caller can
    refuse the edit rather than proceed without a rollback point.
    """
    try:
        staging = p.parent / _STAGING_FOLDER
        staging.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S")
        snap = staging / f"{p.stem}.pre-edit.{stamp}{p.suffix}"
        counter = 1
        while snap.exists():
            snap = staging / f"{p.stem}.pre-edit.{stamp}_{counter}{p.suffix}"
            counter += 1
        shutil.copy2(str(p), str(snap))
        return True, str(snap)
    except OSError as e:
        return False, f"snapshot failed: {e}"


def _guard_edit(path_str: str, tool_name: str) -> tuple[bool, str, Path | None]:
    """Protection + snapshot gate for edit tools.

    Returns (ok, error_or_snapshot_path, resolved_path).
    If ok is False, the caller must return error_or_snapshot_path.
    """
    p = Path(path_str).absolute()
    if not p.exists():
        return False, f"Error: {path_str} does not exist", None
    if not p.is_file():
        return False, f"Error: {path_str} is not a file", None

    prot, reason = is_protected_path(p)
    if prot:
        _audit.log_intercept(tool_name, "protected_edit", path_str, [str(p)], [reason or ""])
        return False, f"Refused: edit of protected path blocked ({reason}). Use sassy_selfmod_edit for controlled edits inside the SassyMCP tree.", None

    ok, snap = _snapshot_before_edit(p)
    if not ok:
        return False, f"Refused: {snap}", None

    _audit.log_intercept(tool_name, "snapshot_before_edit", path_str, [str(p)], [f"snapshot -> {snap}"])
    return True, snap, p


def _find_best_match(content: str, search_text: str, threshold: float = 0.8):
    """Find the best fuzzy match for search_text in content.

    Returns (start_index, end_index, similarity, matched_text) or None.
    """
    search_lines = search_text.splitlines()
    content_lines = content.splitlines()
    search_len = len(search_lines)

    if search_len == 0:
        return None

    best_ratio = 0.0
    best_start = -1
    best_end = -1
    best_matched = ""

    for i in range(len(content_lines) - search_len + 1):
        candidate_lines = content_lines[i : i + search_len]
        candidate = "\n".join(candidate_lines)
        ratio = difflib.SequenceMatcher(None, search_text, candidate).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_start = i
            best_end = i + search_len
            best_matched = candidate

    if best_ratio >= threshold:
        # Convert line indices to character indices
        lines = content.splitlines(keepends=True)
        char_start = sum(len(lines[i]) for i in range(best_start))
        char_end = sum(len(lines[i]) for i in range(best_end))
        return (char_start, char_end, best_ratio, best_matched)
    return None


def _char_diff(expected: str, actual: str) -> str:
    """Produce a character-level diff showing what's different."""
    sm = difflib.SequenceMatcher(None, expected, actual)
    parts = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            text = expected[i1:i2]
            # Show abbreviated context for long equal sections
            if len(text) > 60:
                parts.append(text[:25] + "..." + text[-25:])
            else:
                parts.append(text)
        elif op == "replace":
            parts.append(f"{{-{expected[i1:i2]}-}}{{+{actual[j1:j2]}+}}")
        elif op == "insert":
            parts.append(f"{{+{actual[j1:j2]}+}}")
        elif op == "delete":
            parts.append(f"{{-{expected[i1:i2]}-}}")
    return "".join(parts)


def register(server):
    @server.tool()
    def sassy_edit_block(path: str, old_text: str, new_text: str) -> str:
        """Surgical file edit: find old_text and replace with new_text.

        - Exact match preferred; fuzzy fallback with diff reporting
        - Fails if multiple exact matches found (provide more context)
        - Preserves file encoding and line endings
        - Returns a preview of the change with surrounding context
        - Refuses edits on protected paths
        - Snapshots existing content to _DELETE_/ before applying
        """
        ok, msg, p = _guard_edit(path, "sassy_edit_block")
        if not ok:
            return msg

        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError) as e:
            return f"Error reading {path}: {e}"

        # Try exact match
        count = content.count(old_text)

        if count == 1:
            # Single exact match — apply the edit
            new_content = content.replace(old_text, new_text, 1)
            try:
                p.write_text(new_content, encoding="utf-8")
            except (OSError, PermissionError) as e:
                return f"Error writing {path}: {e}"

            # Build context preview
            idx = content.index(old_text)
            lines_before = content[:idx].splitlines()
            start_line = len(lines_before)
            preview_start = max(0, start_line - 2)
            new_lines = new_content.splitlines()
            end_line = start_line + len(new_text.splitlines())
            preview_end = min(len(new_lines), end_line + 2)
            preview = "\n".join(
                f"{i + 1:>4} | {new_lines[i]}"
                for i in range(preview_start, preview_end)
            )
            return f"Edit applied to {path} (line {start_line + 1}):\n{preview}"

        elif count > 1:
            # Multiple matches — need more context
            positions = []
            start = 0
            for _ in range(min(count, 5)):
                idx = content.index(old_text, start)
                line_num = content[:idx].count("\n") + 1
                positions.append(line_num)
                start = idx + 1
            return (
                f"Error: found {count} matches for the search text in {path}. "
                f"Matches at lines: {positions}. "
                f"Include more surrounding context in old_text to make it unique."
            )

        else:
            # No exact match — try fuzzy
            match = _find_best_match(content, old_text)
            if match:
                _, _, similarity, matched_text = match
                diff = _char_diff(old_text, matched_text)
                return (
                    f"No exact match found in {path}. "
                    f"Closest match ({similarity:.0%} similar):\n"
                    f"Diff: {diff}\n\n"
                    f"Use the exact text from the file for old_text."
                )
            return f"No match found for the search text in {path}."

    @server.tool()
    def sassy_edit_multi(path: str, edits: str) -> str:
        """Apply multiple edits to a file in one call.

        edits format (JSON string):
        [{"old": "text to find", "new": "replacement"}, ...]

        Edits are applied in order. Each must have exactly one match.
        Refuses edits on protected paths and snapshots existing content
        to _DELETE_/ before applying.
        """
        import json as _json

        ok, msg, p = _guard_edit(path, "sassy_edit_multi")
        if not ok:
            return msg

        try:
            edit_list = _json.loads(edits)
        except _json.JSONDecodeError as e:
            return f"Error: invalid JSON in edits: {e}"

        if not isinstance(edit_list, list):
            return "Error: edits must be a JSON array of {old, new} objects"

        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError) as e:
            return f"Error reading {path}: {e}"

        applied = 0
        for i, edit in enumerate(edit_list):
            old = edit.get("old", "")
            new = edit.get("new", "")
            if not old:
                return f"Error: edit {i} has empty 'old' field"
            count = content.count(old)
            if count == 0:
                return f"Error: edit {i} — no match found for old text"
            if count > 1:
                return f"Error: edit {i} — {count} matches found, need more context"
            content = content.replace(old, new, 1)
            applied += 1

        try:
            p.write_text(content, encoding="utf-8")
        except (OSError, PermissionError) as e:
            return f"Error writing {path}: {e}"

        return f"Applied {applied} edits to {path}"
