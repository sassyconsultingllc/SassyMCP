// Cockpit data provider — sources the coordination board from the WAL-aware
// Python (single source of truth: sassymcp.modules.coordination.board_snapshot),
// spawned the same way installer.ts spawns `sassymcp install`. We deliberately
// do NOT read crosslink.db directly: it's in WAL mode, so a file read (sql.js)
// would miss recent, uncheckpointed handoffs — defeating a "live" view.

import * as vscode from "vscode";
import * as path from "path";
import * as fs from "fs";
import { spawn } from "child_process";

export interface Peer {
    peer_id: string;
    name: string;
    platform: string;
    capabilities: string[];
    endpoint: string;
    last_seen: string;
    age_seconds: number;
    alive: boolean;
}

export interface Session {
    session_id: string;
    name: string;
    platform: string;
    last_seen: string;
    created_at: string;
}

export interface Handoff {
    id: number;
    channel: string;
    from: string;
    to: string;
    task: string;
    created_at: string;
    age_seconds: number;
}

export interface Board {
    peers: Peer[];
    channels: { channel: string; count: number }[];
    handoffs: Handoff[];
    sessions: Session[];
    db?: string;
    generated_at?: string;
    error?: string;
}

function looksLikeRepo(p: string): boolean {
    return (
        fs.existsSync(path.join(p, "hermes_node.py")) ||
        fs.existsSync(path.join(p, "sassymcp", "__init__.py")) ||
        fs.existsSync(path.join(p, "sassymcp"))
    );
}

/** Locate the SassyMCP repo root (so `import sassymcp` resolves and we can find hermes_node.py).
 *  Walks up from each workspace folder, so it works whether the open folder is the
 *  repo root or the sassymcp-vscode/ subfolder. */
export function resolveRepo(): string | undefined {
    const cfg = vscode.workspace.getConfiguration("sassymcp");
    const override = (cfg.get<string>("repoPath", "") || "").trim();
    if (override && fs.existsSync(override)) {
        return override;
    }
    const folders = vscode.workspace.workspaceFolders || [];
    for (const f of folders) {
        let cur = f.uri.fsPath;
        for (let i = 0; i < 3; i++) {
            if (looksLikeRepo(cur)) {
                return cur;
            }
            const parent = path.dirname(cur);
            if (parent === cur) {
                break;
            }
            cur = parent;
        }
    }
    return folders[0]?.uri.fsPath;
}

/** Resolve the Python interpreter: setting > repo .venv > PATH python. */
export function resolvePython(repo: string | undefined): string {
    const cfg = vscode.workspace.getConfiguration("sassymcp");
    const override = (cfg.get<string>("pythonPath", "") || "").trim();
    if (override) {
        return override;
    }
    if (repo) {
        const win = path.join(repo, ".venv", "Scripts", "python.exe");
        const nix = path.join(repo, ".venv", "bin", "python");
        if (fs.existsSync(win)) {
            return win;
        }
        if (fs.existsSync(nix)) {
            return nix;
        }
    }
    return process.platform === "win32" ? "python" : "python3";
}

function emptyBoard(error: string): Board {
    return { peers: [], channels: [], handoffs: [], sessions: [], error };
}

/** Register this VS Code instance as an observer peer (fire-and-forget, TTL'd). */
export function announceSelf(ttlSeconds = 600): void {
    const repo = resolveRepo();
    const py = resolvePython(repo);
    try {
        const proc = spawn(
            py,
            [
                "-m", "sassymcp.modules.coordination", "announce",
                "--id", "vscode-cockpit", "--name", "VS Code Cockpit",
                "--platform", "vscode", "--caps", "observer,cockpit",
                "--ttl", String(ttlSeconds),
            ],
            { cwd: repo, shell: false }
        );
        proc.on("error", () => { /* best effort */ });
    } catch {
        /* best effort */
    }
}

export interface BrainStatus {
    version?: string;
    tier?: string;
    addons?: string[];
    license_valid?: boolean;
    email?: string;
    memory_count?: number;
    milestones?: number;
    projects?: string[];
    audit_count?: number;
    persona?: boolean;
    groups?: { name: string; module_count: number; always_load: boolean; allowed: boolean; description: string }[];
    group_count?: number;
    allowed_group_count?: number;
    module_total?: number;
    module_allowed?: number;
    home?: string;
    error?: string;
}

/** Spawn a python -m entry that prints one JSON line. Never rejects — resolves
 *  the parsed object, or an object with `.error` set on any failure. */
function spawnJson(args: string[], timeoutMs: number): Promise<any> {
    const repo = resolveRepo();
    const py = resolvePython(repo);
    return new Promise((resolve) => {
        let stdout = "";
        let stderr = "";
        let done = false;
        const finish = (v: any) => {
            if (done) {
                return;
            }
            done = true;
            clearTimeout(timer);
            resolve(v);
        };
        let proc;
        try {
            proc = spawn(py, args, { cwd: repo, shell: false });
        } catch (e) {
            return resolve({ error: `could not spawn ${py}: ${String(e)}` });
        }
        const timer = setTimeout(() => {
            try { proc.kill(); } catch { /* noop */ }
            finish({ error: `timed out after ${timeoutMs}ms (python=${py}, repo=${repo || "?"})` });
        }, timeoutMs);
        proc.stdout.on("data", (d) => { stdout += d.toString(); });
        proc.stderr.on("data", (d) => { stderr += d.toString(); });
        proc.on("error", (err) => finish({ error: `spawn failed (${py}): ${err.message}` }));
        proc.on("close", (code) => {
            if (code !== 0 && !stdout.trim()) {
                finish({ error: `python exit ${code}: ${stderr.slice(0, 300) || "no output"}` });
                return;
            }
            try {
                finish(JSON.parse(stdout));
            } catch (e) {
                finish({ error: `bad JSON: ${String(e)} :: ${stdout.slice(0, 200)}` });
            }
        });
    });
}

/** Poll the coordination board. Never rejects — returns a Board with `.error` set on failure. */
export async function getBoard(timeoutMs = 12000): Promise<Board> {
    const v = await spawnJson(["-m", "sassymcp.modules.coordination"], timeoutMs);
    return v && !v.error ? (v as Board) : emptyBoard(v?.error || "unknown error");
}

/** Read the brain status snapshot (tier, memory, audit, groups). */
export async function getBrainStatus(timeoutMs = 12000): Promise<BrainStatus> {
    const v = await spawnJson(["-m", "sassymcp._brain_status"], timeoutMs);
    return (v && typeof v === "object") ? (v as BrainStatus) : { error: "unknown error" };
}
