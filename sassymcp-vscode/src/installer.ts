// Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
// Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
// CodeMark: SCLLC1-SassyMCP-DXMG72W3DB7O
// Locates sassymcp.exe and runs `sassymcp install` to patch every
// detected MCP client config. Spawns the install CLI as a subprocess
// rather than reimplementing the logic — single source of truth lives in
// sassymcp/install.py.

import * as path from "path";
import * as fs from "fs";
import * as os from "os";
import { spawn } from "child_process";

export interface InstallResult {
    ok: boolean;
    summary: string;     // human-readable: e.g. "Patched 5 of 8 clients"
    error?: string;      // populated if ok=false
    rawOutput?: string;
}

export class Installer {
    constructor(private exePathOverride: string) {}

    async locateExe(): Promise<string | undefined> {
        // 1. Explicit override
        if (this.exePathOverride && fs.existsSync(this.exePathOverride)) {
            return this.exePathOverride;
        }

        // 2. PATH lookup
        const pathDirs = (process.env.PATH || "").split(path.delimiter);
        const exeName = process.platform === "win32" ? "sassymcp.exe" : "sassymcp";
        for (const dir of pathDirs) {
            if (!dir) continue;
            const candidate = path.join(dir, exeName);
            if (fs.existsSync(candidate)) return candidate;
        }

        // 3. Common install locations on Windows
        if (process.platform === "win32") {
            const localAppData = process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local");
            const candidates = [
                path.join(localAppData, "SassyMCP", "sassymcp.exe"),
                path.join("C:\\", "Program Files", "SassyMCP", "sassymcp.exe"),
            ];
            for (const c of candidates) {
                if (fs.existsSync(c)) return c;
            }
        }

        return undefined;
    }

    async runInstall(exePath: string): Promise<InstallResult> {
        // Prefer the install entry point (sassymcp install). On the frozen exe,
        // we shell into `sassymcp.exe -m sassymcp.install` would not work —
        // the frozen entry hardcoded sassymcp.server:main. Instead use the
        // separate sassymcp-install entry from the wheel install, OR fall back
        // to invoking the exe with a special arg if neither exists.
        //
        // Pragmatic approach: ship sassymcp-install alongside sassymcp in the
        // installer/zip, and try both names. Otherwise, give a clear error.

        const dir = path.dirname(exePath);
        const installExe = process.platform === "win32" ? "sassymcp-install.exe" : "sassymcp-install";
        const installCmd = path.join(dir, installExe);

        let cmd: string;
        let args: string[];
        if (fs.existsSync(installCmd)) {
            cmd = installCmd;
            args = ["--json"];
        } else {
            // Fall back to invoking python via the wheel install. If the user
            // has the wheel installed alongside the exe (e.g., via pip install
            // sassymcp), python -m sassymcp.install works.
            cmd = "python";
            args = ["-m", "sassymcp.install", "--json"];
        }

        return new Promise((resolve) => {
            let stdout = "";
            let stderr = "";
            const proc = spawn(cmd, args, { shell: false });
            proc.stdout.on("data", (d) => { stdout += d.toString(); });
            proc.stderr.on("data", (d) => { stderr += d.toString(); });
            proc.on("error", (err) => {
                resolve({ ok: false, summary: "", error: `Could not spawn '${cmd}': ${err.message}`, rawOutput: stderr });
            });
            proc.on("close", (code) => {
                if (code === 0) {
                    try {
                        const results = JSON.parse(stdout);
                        const patched = results.filter((r: any) => r.action === "added" || r.action === "updated").length;
                        const total = results.length;
                        resolve({ ok: true, summary: `Patched ${patched} of ${total} clients`, rawOutput: stdout });
                    } catch (parseErr) {
                        resolve({ ok: false, summary: "", error: `parse failed: ${parseErr}`, rawOutput: stdout });
                    }
                } else {
                    resolve({ ok: false, summary: "", error: `exit ${code}: ${stderr || stdout}`, rawOutput: stdout });
                }
            });
        });
    }
}
