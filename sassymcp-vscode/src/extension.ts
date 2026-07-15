// Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
// Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
// CodeMark: SCLLC1-SassyMCP-22GNF3FD3SX4
// SassyMCP VS Code extension entry point.
//
// On activation:
// 1. Locate sassymcp.exe (PATH or sassymcp.exePath setting)
// 2. If not found, surface a helpful warning notification
// 3. Otherwise: run `sassymcp install` to patch every detected MCP client config
// 4. Initialize the status bar
// 5. Register all commands

import * as vscode from "vscode";
import { Installer } from "./installer";
import { StatusManager } from "./status";
import { Brain } from "./brain";
import { SetupWizardPanel } from "./setupWizard";
import { BrainCockpitPanel } from "./cockpit";

let statusManager: StatusManager | undefined;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
    const cfg = vscode.workspace.getConfiguration("sassymcp");
    const exePathOverride = cfg.get<string>("exePath", "").trim();
    const runInstall = cfg.get<boolean>("runInstallOnActivation", true);
    const runOnce = cfg.get<boolean>("installRunOnce", true);

    const installer = new Installer(exePathOverride);
    const brain = new Brain();

    // Locate exe
    const exePath = await installer.locateExe();
    if (!exePath) {
        vscode.window.showWarningMessage(
            "SassyMCP: could not locate sassymcp.exe. Set 'sassymcp.exePath' in settings, or install from sassyconsultingllc.com.",
            "Open Settings"
        ).then(choice => {
            if (choice === "Open Settings") {
                vscode.commands.executeCommand("workbench.action.openSettings", "sassymcp");
            }
        });
    } else {
        // Run install if enabled
        if (runInstall) {
            const marker = "sassymcp.installedOnce";
            const alreadyRan = runOnce && context.globalState.get<boolean>(marker, false);
            if (!alreadyRan) {
                installer.runInstall(exePath).then((result) => {
                    if (result.ok) {
                        if (runOnce) {
                            context.globalState.update(marker, true);
                        }
                    } else {
                        vscode.window.showErrorMessage(
                            `SassyMCP: 'sassymcp install' failed: ${result.error}`
                        );
                    }
                });
            }
        }
    }

    // Status bar
    statusManager = new StatusManager(exePath, brain);
    statusManager.start();
    context.subscriptions.push(statusManager);

    // Commands
    context.subscriptions.push(
        vscode.commands.registerCommand("sassymcp.openCockpit", () => {
            BrainCockpitPanel.createOrShow(context.extensionUri);
        }),
        vscode.commands.registerCommand("sassymcp.runSetupWizard", () => {
            if (!exePath) {
                vscode.window.showErrorMessage("SassyMCP: sassymcp.exe not found.");
                return;
            }
            SetupWizardPanel.createOrShow(context.extensionUri, exePath);
        }),
        vscode.commands.registerCommand("sassymcp.reinstallConfigs", async () => {
            if (!exePath) {
                vscode.window.showErrorMessage("SassyMCP: sassymcp.exe not found.");
                return;
            }
            const result = await installer.runInstall(exePath);
            if (result.ok) {
                vscode.window.showInformationMessage(
                    `SassyMCP: reinstall complete. ${result.summary}`
                );
            } else {
                vscode.window.showErrorMessage(
                    `SassyMCP: reinstall failed: ${result.error}`
                );
            }
            statusManager?.refresh();
        }),
        vscode.commands.registerCommand("sassymcp.openAuditLog", async () => {
            const auditPath = brain.auditLogPath();
            try {
                const doc = await vscode.workspace.openTextDocument(auditPath);
                await vscode.window.showTextDocument(doc);
            } catch (err) {
                vscode.window.showWarningMessage(
                    `SassyMCP: audit log not found at ${auditPath}. It will be created on first tool call.`
                );
            }
        }),
        vscode.commands.registerCommand("sassymcp.openDeleteFolder", async () => {
            const deleteDir = brain.deleteStagingDir();
            try {
                const uri = vscode.Uri.file(deleteDir);
                await vscode.commands.executeCommand("revealFileInOS", uri);
            } catch (err) {
                vscode.window.showWarningMessage(
                    `SassyMCP: _DELETE_ folder not found at ${deleteDir}.`
                );
            }
        }),
        vscode.commands.registerCommand("sassymcp.showBrainStatus", async () => {
            const state = await brain.getState();
            const lines = [
                `**Tier:** ${state.tier}`,
                `**Memory entries:** ${state.memoryCount}`,
                `**Recent audit:** ${state.recentAuditCount} (last 100)`,
                `**Persona:** ${state.hasPersona ? "configured" : "not yet configured — run Setup Wizard"}`,
                `**Sassy home:** ${state.home}`,
            ];
            vscode.window.showInformationMessage(lines.join("\n"), { modal: true });
        }),
    );
}

export function deactivate(): void {
    statusManager?.dispose();
}
