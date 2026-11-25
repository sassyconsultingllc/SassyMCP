// Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
// Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
// CodeMark: SCLLC1-SassyMCP-25JAFPM52KBR
// Status bar item showing tier and brain health. Polls every 30s.

import * as vscode from "vscode";
import { Brain } from "./brain";

export class StatusManager implements vscode.Disposable {
    private item: vscode.StatusBarItem;
    private timer: NodeJS.Timeout | undefined;

    constructor(private exePath: string | undefined, private brain: Brain) {
        this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
        this.item.command = "sassymcp.openCockpit";
    }

    start(): void {
        this.refresh();
        this.timer = setInterval(() => this.refresh(), 30_000);
    }

    async refresh(): Promise<void> {
        if (!this.exePath) {
            this.item.text = "$(warning) SassyMCP: Not Installed";
            this.item.tooltip = "Set sassymcp.exePath or install from sassyconsultingllc.com";
            this.item.show();
            return;
        }
        try {
            const state = await this.brain.getState();
            const tierLabel = state.tier === "unknown" ? "Free" : state.tier.charAt(0).toUpperCase() + state.tier.slice(1);
            this.item.text = `$(zap) SassyMCP: ${tierLabel}`;
            this.item.tooltip = [
                `Tier: ${state.tier}`,
                `Persona: ${state.hasPersona ? "configured" : "not configured"}`,
                `Recent audit: ${state.recentAuditCount}`,
                "Click for full status",
            ].join("\n");
            this.item.show();
        } catch (e) {
            this.item.text = "$(warning) SassyMCP: Error";
            this.item.tooltip = `Could not read brain state: ${e}`;
            this.item.show();
        }
    }

    dispose(): void {
        if (this.timer) {
            clearInterval(this.timer);
        }
        this.item.dispose();
    }
}
