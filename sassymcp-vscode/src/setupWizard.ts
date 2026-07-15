// Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
// Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
// CodeMark: SCLLC1-SassyMCP-4LVARUIHK536
// Setup Wizard webview -- single-page form that mirrors sassy_setup_wizard's
// questionnaire, posts answers to sassymcp setup-wizard, and shows the
// resulting persona.md in a read-only editor.
//
// MVP scope: plain HTML form, no React, no build step. The form submits via
// vscode.postMessage; the host runs sassymcp and returns the generated persona.
// All dynamic DOM updates use textContent / createElement -- no innerHTML.

import * as vscode from "vscode";
import * as path from "path";
import { spawn } from "child_process";

export class SetupWizardPanel {
    public static currentPanel: SetupWizardPanel | undefined;
    public static readonly viewType = "sassymcpSetupWizard";

    private readonly panel: vscode.WebviewPanel;
    private readonly extensionUri: vscode.Uri;
    private disposables: vscode.Disposable[] = [];

    public static createOrShow(extensionUri: vscode.Uri, exePath: string): void {
        const column = vscode.window.activeTextEditor?.viewColumn;

        if (SetupWizardPanel.currentPanel) {
            SetupWizardPanel.currentPanel.panel.reveal(column);
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            SetupWizardPanel.viewType,
            "SassyMCP Setup",
            column || vscode.ViewColumn.One,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
                localResourceRoots: [vscode.Uri.joinPath(extensionUri, "resources")],
            }
        );

        SetupWizardPanel.currentPanel = new SetupWizardPanel(panel, extensionUri, exePath);
    }

    private constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri, private exePath: string) {
        this.panel = panel;
        this.extensionUri = extensionUri;

        this.panel.webview.html = this.getHtml();

        this.panel.onDidDispose(() => this.dispose(), null, this.disposables);

        this.panel.webview.onDidReceiveMessage(
            async (msg) => {
                if (msg.type === "submit") {
                    await this.handleSubmit(msg.fields);
                }
            },
            null,
            this.disposables
        );
    }

    private async handleSubmit(fields: Record<string, string>): Promise<void> {
        // Build CLI args from the form fields.
        // sassy_setup_wizard accepts: role, expertise_level, specializations,
        // languages, frameworks, communication_style, security_posture,
        // mcp_clients, notes.
        const args: string[] = ["setup-wizard"];
        for (const [key, val] of Object.entries(fields)) {
            if (val && val.trim().length > 0) {
                args.push("--" + key.replace(/_/g, "-"));
                args.push(val.trim());
            }
        }

        this.panel.webview.postMessage({ type: "status", text: "Running setup..." });

        const result = await this.spawnSassy(args);
        if (result.code !== 0) {
            this.panel.webview.postMessage({
                type: "result",
                ok: false,
                error: "sassymcp exited " + result.code + ": " + result.stderr.slice(0, 500),
            });
            return;
        }

        // Read the generated persona.md
        const sassyHome = process.env.SASSYMCP_HOME || path.join(require("os").homedir(), ".sassymcp");
        const personaPath = path.join(sassyHome, "persona.md");

        try {
            const fs = require("fs");
            const personaContent: string = fs.readFileSync(personaPath, "utf-8");
            this.panel.webview.postMessage({
                type: "result",
                ok: true,
                persona: personaContent,
            });

            // Also open it in an editor for the user
            const doc = await vscode.workspace.openTextDocument(personaPath);
            await vscode.window.showTextDocument(doc, vscode.ViewColumn.Beside);
        } catch (e) {
            this.panel.webview.postMessage({
                type: "result",
                ok: false,
                error: "Setup completed but persona.md not found at " + personaPath + ": " + e,
            });
        }
    }

    private spawnSassy(args: string[]): Promise<{ stdout: string; stderr: string; code: number }> {
        return new Promise((resolve) => {
            let stdout = "";
            let stderr = "";
            const proc = spawn(this.exePath, args, { shell: false });
            proc.stdout.on("data", (d) => { stdout += d.toString(); });
            proc.stderr.on("data", (d) => { stderr += d.toString(); });
            proc.on("close", (code) => resolve({ stdout, stderr, code: code ?? -1 }));
            proc.on("error", (err) => resolve({ stdout, stderr: err.message, code: -1 }));
        });
    }

    public dispose(): void {
        SetupWizardPanel.currentPanel = undefined;
        this.panel.dispose();
        for (const d of this.disposables) {
            d.dispose();
        }
    }

    private getHtml(): string {
        const csp = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline';";
        return [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="UTF-8">',
            '<meta http-equiv="Content-Security-Policy" content="' + csp + '">',
            "<title>SassyMCP Setup</title>",
            "<style>",
            "body{font-family:var(--vscode-font-family);padding:24px;max-width:720px;margin:0 auto;",
            "     color:var(--vscode-editor-foreground);background:var(--vscode-editor-background)}",
            "h1{font-size:24px;margin-bottom:8px}",
            "p.subtitle{color:var(--vscode-descriptionForeground);margin-top:0;margin-bottom:24px}",
            ".field{display:flex;flex-direction:column;margin-bottom:16px}",
            "label{font-weight:600;margin-bottom:4px}",
            ".hint{color:var(--vscode-descriptionForeground);font-size:12px;margin-top:4px}",
            "input[type=text],select,textarea{background:var(--vscode-input-background);",
            "  color:var(--vscode-input-foreground);border:1px solid var(--vscode-input-border,transparent);",
            "  padding:6px 8px;font-family:inherit;font-size:13px}",
            "textarea{min-height:60px;resize:vertical}",
            "button{background:var(--vscode-button-background);color:var(--vscode-button-foreground);",
            "  border:none;padding:8px 16px;font-weight:600;cursor:pointer}",
            "button:hover{background:var(--vscode-button-hoverBackground)}",
            "#status{margin-top:16px;padding:12px;background:var(--vscode-textBlockQuote-background);",
            "  border-left:4px solid var(--vscode-textBlockQuote-border);display:none}",
            "#status.show{display:block}",
            "#result{margin-top:16px;padding:12px;display:none}",
            "#result.ok{display:block;background:var(--vscode-textBlockQuote-background);",
            "  border-left:4px solid var(--vscode-charts-green,#4caf50)}",
            "#result.error{display:block;background:var(--vscode-inputValidation-errorBackground);",
            "  border-left:4px solid var(--vscode-inputValidation-errorBorder)}",
            "pre{white-space:pre-wrap;font-family:var(--vscode-editor-font-family);font-size:12px}",
            "</style>",
            "</head>",
            "<body>",
            "<h1>SassyMCP Setup</h1>",
            '<p class="subtitle">Tell SassyMCP about how you work.',
            "This generates ~/.sassymcp/persona.md, which every AI session reads.</p>",
            '<form id="form">',
            '  <div class="field">',
            '    <label for="role">Role</label>',
            '    <select id="role" name="role">',
            '      <option value="developer">Developer</option>',
            '      <option value="sysadmin">SysAdmin</option>',
            '      <option value="security">Security</option>',
            '      <option value="devops">DevOps</option>',
            '      <option value="data">Data</option>',
            '      <option value="designer">Designer</option>',
            '      <option value="manager">Manager</option>',
            '      <option value="other">Other</option>',
            "    </select>",
            "  </div>",
            '  <div class="field">',
            '    <label for="expertise_level">Expertise level</label>',
            '    <select id="expertise_level" name="expertise_level">',
            '      <option value="junior">Junior</option>',
            '      <option value="mid">Mid</option>',
            '      <option value="senior" selected>Senior</option>',
            '      <option value="principal">Principal</option>',
            '      <option value="staff">Staff</option>',
            "    </select>",
            "  </div>",
            '  <div class="field">',
            '    <label for="languages">Languages</label>',
            '    <input type="text" id="languages" name="languages" placeholder="e.g. Python, Rust, TypeScript">',
            '    <div class="hint">Comma-separated.</div>',
            "  </div>",
            '  <div class="field">',
            '    <label for="frameworks">Frameworks / tools</label>',
            '    <input type="text" id="frameworks" name="frameworks" placeholder="e.g. React, FastAPI, Cloudflare Workers">',
            '    <div class="hint">Comma-separated.</div>',
            "  </div>",
            '  <div class="field">',
            '    <label for="communication_style">Communication style</label>',
            '    <select id="communication_style" name="communication_style">',
            '      <option value="terse" selected>Terse -- code only</option>',
            '      <option value="balanced">Balanced -- brief explanations</option>',
            '      <option value="verbose">Verbose -- detailed rationale</option>',
            "    </select>",
            "  </div>",
            '  <div class="field">',
            '    <label for="security_posture">Security posture</label>',
            '    <select id="security_posture" name="security_posture">',
            '      <option value="standard" selected>Standard (OWASP)</option>',
            '      <option value="hardened">Hardened (+ CSP, HSTS, rate limit)</option>',
            '      <option value="paranoid">Paranoid (+ air-gap, cert pinning, zero trust)</option>',
            "    </select>",
            "  </div>",
            '  <div class="field">',
            '    <label for="notes">Notes</label>',
            '    <textarea id="notes" name="notes" placeholder="Anything else SassyMCP should know about how you work."></textarea>',
            "  </div>",
            '  <button type="submit">Generate persona.md</button>',
            "</form>",
            '<div id="status"></div>',
            '<div id="result"></div>',
            "<script>",
            "const vscode = acquireVsCodeApi();",
            'const form = document.getElementById("form");',
            'const statusEl = document.getElementById("status");',
            'const resultEl = document.getElementById("result");',
            "",
            'form.addEventListener("submit", (e) => {',
            "  e.preventDefault();",
            "  const fields = Object.fromEntries(new FormData(form).entries());",
            '  statusEl.textContent = "Submitting...";',
            '  statusEl.classList.add("show");',
            '  resultEl.className = "";',
            "  while (resultEl.firstChild) { resultEl.removeChild(resultEl.firstChild); }",
            '  vscode.postMessage({ type: "submit", fields });',
            "});",
            "",
            'window.addEventListener("message", (event) => {',
            "  const msg = event.data;",
            '  if (msg.type === "status") {',
            "    statusEl.textContent = msg.text;",
            '    statusEl.classList.add("show");',
            '  } else if (msg.type === "result") {',
            '    statusEl.classList.remove("show");',
            "    while (resultEl.firstChild) { resultEl.removeChild(resultEl.firstChild); }",
            "    if (msg.ok) {",
            '      resultEl.className = "ok";',
            '      const strong = document.createElement("strong");',
            '      strong.textContent = "Done. persona.md generated and opened in editor.";',
            '      const pre = document.createElement("pre");',
            '      pre.textContent = (msg.persona || "").slice(0, 4000);',
            "      resultEl.appendChild(strong);",
            "      resultEl.appendChild(pre);",
            "    } else {",
            '      resultEl.className = "error";',
            '      const strong = document.createElement("strong");',
            '      strong.textContent = "Error. ";',
            '      const span = document.createElement("span");',
            '      span.textContent = msg.error || "(unknown)";',
            "      resultEl.appendChild(strong);",
            "      resultEl.appendChild(span);",
            "    }",
            "  }",
            "});",
            "<\/script>",
            "</body>",
            "</html>",
        ].join("\n");
    }
}
