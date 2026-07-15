// Hermes controller — starts/stops the local Ollama peer node (hermes_node.py)
// as a child process and streams its output to the cockpit. This is the
// "second head": Claude leads, Hermes joins the `joint` crosslink channel.

import * as vscode from "vscode";
import * as path from "path";
import * as fs from "fs";
import { ChildProcess, spawn } from "child_process";
import { resolvePython, resolveRepo } from "./cockpitData";

export interface HermesEvent {
    type: "status" | "log";
    running: boolean;
    line?: string;
}

export class HermesController {
    private proc: ChildProcess | undefined;

    constructor(private onEvent: (e: HermesEvent) => void) {}

    get running(): boolean {
        return this.proc !== undefined;
    }

    start(): { ok: boolean; error?: string } {
        if (this.proc) {
            return { ok: true };
        }
        const repo = resolveRepo();
        const cfg = vscode.workspace.getConfiguration("sassymcp");
        const hermesPath =
            (cfg.get<string>("hermesNodePath", "") || "").trim() ||
            (repo ? path.join(repo, "hermes_node.py") : "");
        if (!hermesPath || !fs.existsSync(hermesPath)) {
            return {
                ok: false,
                error: `hermes_node.py not found${hermesPath ? ` at ${hermesPath}` : ""}. Set "sassymcp.hermesNodePath" or "sassymcp.repoPath".`,
            };
        }
        const py = resolvePython(repo);
        const autorun = cfg.get<boolean>("hermesAutorun", false) ? "1" : "0";
        try {
            this.proc = spawn(py, [hermesPath], {
                cwd: repo,
                shell: false,
                env: { ...process.env, HERMES_AUTORUN: autorun },
            });
        } catch (e) {
            this.proc = undefined;
            return { ok: false, error: String(e) };
        }
        this.onEvent({ type: "status", running: true, line: `starting hermes (${py})` });
        const pump = (d: Buffer) => {
            d.toString()
                .split(/\r?\n/)
                .filter((l) => l.trim().length > 0)
                .forEach((line) => this.onEvent({ type: "log", running: true, line }));
        };
        this.proc.stdout?.on("data", pump);
        this.proc.stderr?.on("data", pump);
        this.proc.on("close", (code) => {
            this.proc = undefined;
            this.onEvent({ type: "status", running: false, line: `hermes exited (${code})` });
        });
        this.proc.on("error", (err) => {
            this.proc = undefined;
            this.onEvent({ type: "status", running: false, line: `hermes error: ${err.message}` });
        });
        return { ok: true };
    }

    stop(): void {
        if (this.proc) {
            try { this.proc.kill(); } catch { /* noop */ }
            this.proc = undefined;
            this.onEvent({ type: "status", running: false, line: "stopped" });
        }
    }

    dispose(): void {
        this.stop();
    }
}
