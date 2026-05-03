// Read-only view of ~/.sassymcp/ for the status bar and the Show Brain Status
// command. We never write here — all mutation goes through sassymcp's tools.

import * as fs from "fs";
import * as os from "os";
import * as path from "path";

export interface BrainState {
    home: string;
    tier: "free" | "pro" | "forensics" | "unknown";
    memoryCount: number;
    recentAuditCount: number;
    hasPersona: boolean;
}

export class Brain {
    home(): string {
        return process.env.SASSYMCP_HOME || path.join(os.homedir(), ".sassymcp");
    }

    auditLogPath(): string {
        return path.join(this.home(), "audit.log");
    }

    deleteStagingDir(): string {
        return path.join(this.home(), "_DELETE_");
    }

    async getState(): Promise<BrainState> {
        const home = this.home();
        const state: BrainState = {
            home,
            tier: "unknown",
            memoryCount: 0,
            recentAuditCount: 0,
            hasPersona: false,
        };

        // License tier
        const licensePath = path.join(home, "license.json");
        if (fs.existsSync(licensePath)) {
            try {
                const raw = JSON.parse(fs.readFileSync(licensePath, "utf-8"));
                if (raw.tier === "free" || raw.tier === "pro" || raw.tier === "forensics") {
                    state.tier = raw.tier;
                } else {
                    state.tier = "free";
                }
            } catch (e) {
                state.tier = "free";
            }
        } else {
            state.tier = "free";
        }

        // Persona
        const personaPath = path.join(home, "persona.md");
        state.hasPersona = fs.existsSync(personaPath) && fs.statSync(personaPath).size > 50;

        // Memory count — quick line count of memory db is not feasible without
        // sqlite3; use the audit log line count as a proxy for activity, and
        // count entries in the audit log tail (last 100 lines).
        const auditLogPath = path.join(home, "audit.log");
        if (fs.existsSync(auditLogPath)) {
            try {
                const stat = fs.statSync(auditLogPath);
                // Read last 64KB of the file to count recent entries
                const fd = fs.openSync(auditLogPath, "r");
                try {
                    const tailSize = Math.min(64 * 1024, stat.size);
                    const buf = Buffer.alloc(tailSize);
                    fs.readSync(fd, buf, 0, tailSize, stat.size - tailSize);
                    const lines = buf.toString("utf-8").split("\n").filter((l) => l.trim().length > 0);
                    state.recentAuditCount = Math.min(lines.length, 100);
                } finally {
                    fs.closeSync(fd);
                }
            } catch (e) {
                state.recentAuditCount = 0;
            }
        }

        // Memory count: read first row of memory.db schema if present.
        // SQLite parsing in pure JS is nontrivial — for the MVP we just check
        // for file existence and report ">0" when the file exists. A future
        // sub-project (#5 webview) will use better-sqlite3 for real counts.
        const memDbPath = path.join(home, "memory.db");
        if (fs.existsSync(memDbPath) && fs.statSync(memDbPath).size > 4096) {
            // Schema-only DBs are ~4KB; anything bigger has at least one row.
            state.memoryCount = -1; // sentinel: "has data, count unavailable"
        }

        return state;
    }
}
