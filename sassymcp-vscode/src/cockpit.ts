// Sassy Brain Cockpit — webview panel hosting the React coordination view.
// The webview is built from webview/ by Vite into media/cockpit/ (cockpit.js +
// cockpit.css). The host polls the coordination board and relays Hermes events.

import * as vscode from "vscode";
import { announceSelf, getBoard } from "./cockpitData";
import { HermesController } from "./hermes";

const POLL_MS = 4000;
const REANNOUNCE_MS = 45000;

export class BrainCockpitPanel {
    public static current: BrainCockpitPanel | undefined;
    public static readonly viewType = "sassymcpCockpit";

    private readonly panel: vscode.WebviewPanel;
    private disposables: vscode.Disposable[] = [];
    private timer: ReturnType<typeof setInterval> | undefined;
    private lastAnnounce = 0;
    private readonly hermes: HermesController;

    public static createOrShow(extensionUri: vscode.Uri): void {
        const column = vscode.window.activeTextEditor?.viewColumn;
        if (BrainCockpitPanel.current) {
            BrainCockpitPanel.current.panel.reveal(column);
            return;
        }
        const panel = vscode.window.createWebviewPanel(
            BrainCockpitPanel.viewType,
            "Sassy Brain Cockpit",
            column || vscode.ViewColumn.One,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
                localResourceRoots: [vscode.Uri.joinPath(extensionUri, "media")],
            }
        );
        BrainCockpitPanel.current = new BrainCockpitPanel(panel, extensionUri);
    }

    private constructor(panel: vscode.WebviewPanel, private readonly extensionUri: vscode.Uri) {
        this.panel = panel;
        this.hermes = new HermesController((e) => this.post({ type: "hermes", running: e.running, line: e.line }));
        this.panel.webview.html = this.html();

        this.panel.onDidDispose(() => this.dispose(), null, this.disposables);

        this.panel.webview.onDidReceiveMessage(async (msg: any) => {
            switch (msg?.type) {
                case "ready":
                    await this.refresh();
                    this.post({ type: "hermes", running: this.hermes.running });
                    this.startPolling();
                    break;
                case "refresh":
                    await this.refresh();
                    break;
                case "startHermes": {
                    const r = this.hermes.start();
                    if (!r.ok) {
                        this.post({ type: "hermes", running: false, line: r.error });
                        vscode.window.showErrorMessage(`SassyMCP: ${r.error}`);
                    }
                    break;
                }
                case "stopHermes":
                    this.hermes.stop();
                    break;
            }
        }, null, this.disposables);
    }

    private startPolling(): void {
        if (this.timer) {
            return;
        }
        this.timer = setInterval(() => { void this.refresh(); }, POLL_MS);
    }

    private async refresh(): Promise<void> {
        const now = Date.now();
        if (now - this.lastAnnounce > REANNOUNCE_MS) {
            announceSelf();
            this.lastAnnounce = now;
        }
        const board = await getBoard();
        this.post({ type: "board", data: board });
    }

    private post(m: any): void {
        void this.panel.webview.postMessage(m);
    }

    private html(): string {
        const w = this.panel.webview;
        const script = w.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "cockpit", "cockpit.js"));
        const style = w.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "cockpit", "cockpit.css"));
        const nonce = nonceStr();
        const csp = [
            "default-src 'none'",
            `img-src ${w.cspSource} data: https:`,
            `style-src ${w.cspSource} 'unsafe-inline'`,
            `font-src ${w.cspSource}`,
            `script-src 'nonce-${nonce}'`,
        ].join("; ");
        return [
            "<!DOCTYPE html>",
            '<html lang="en"><head>',
            '<meta charset="UTF-8">',
            `<meta http-equiv="Content-Security-Policy" content="${csp}">`,
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
            `<link rel="stylesheet" href="${style}">`,
            "<title>Sassy Brain Cockpit</title>",
            "</head><body><div id=\"root\"></div>",
            `<script nonce="${nonce}" src="${script}"></script>`,
            "</body></html>",
        ].join("\n");
    }

    public dispose(): void {
        BrainCockpitPanel.current = undefined;
        if (this.timer) {
            clearInterval(this.timer);
        }
        this.hermes.dispose();
        this.panel.dispose();
        for (const d of this.disposables) {
            d.dispose();
        }
    }
}

function nonceStr(): string {
    let t = "";
    const c = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    for (let i = 0; i < 32; i++) {
        t += c.charAt(Math.floor(Math.random() * c.length));
    }
    return t;
}
