"use strict";
/**
 * RAAG for VS Code — entry point.
 *
 * The extension owns exactly two things: invoking the CLI (raagRunner) and
 * rendering the result (decorations). No coupling or instability arithmetic
 * lives in this file, or anywhere in the extension — that would be a second,
 * unverified implementation of logic the Python engine already gets right.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = require("vscode");
const raagRunner_1 = require("./raagRunner");
const decorations_1 = require("./decorations");
let lastReport;
let decorator;
function activate(context) {
    decorator = new decorations_1.InstabilityDecorator();
    const analyzeCommand = vscode.commands.registerCommand("raag.analyzeWorkspace", async () => {
        const folder = vscode.workspace.workspaceFolders?.[0];
        if (!folder) {
            vscode.window.showErrorMessage("RAAG: open a folder or workspace first.");
            return;
        }
        await vscode.window.withProgress({
            location: vscode.ProgressLocation.Notification,
            title: "RAAG: analyzing workspace",
            cancellable: false,
        }, async () => {
            try {
                const report = await (0, raagRunner_1.analyzeWorkspace)(folder.uri.fsPath, (0, raagRunner_1.getConfiguredCliPath)());
                lastReport = report;
                for (const editor of vscode.window.visibleTextEditors) {
                    decorator.applyTo(editor, report);
                }
                const violations = report.violations ?? [];
                const errorCount = violations.filter((v) => v.severity === "error").length;
                const message = errorCount === 0
                    ? "RAAG: analysis complete, no violations."
                    : `RAAG: analysis complete, ${errorCount} violation(s) found.`;
                vscode.window.showInformationMessage(message);
            }
            catch (error) {
                if (error instanceof raagRunner_1.RaagCliError) {
                    vscode.window.showErrorMessage(`RAAG: ${error.message}`);
                }
                else {
                    vscode.window.showErrorMessage(`RAAG: unexpected error — ${String(error)}`);
                }
            }
        });
    });
    const clearCommand = vscode.commands.registerCommand("raag.clearDecorations", () => {
        lastReport = undefined;
        for (const editor of vscode.window.visibleTextEditors) {
            decorator.clear(editor);
        }
    });
    // Re-apply decorations when a file is opened or the active editor changes,
    // using the most recent report already in memory — this does not trigger
    // a new analysis, since re-running the CLI on every tab switch would be
    // both slow and surprising. A fresh analysis is always an explicit action.
    const editorChangeListener = vscode.window.onDidChangeVisibleTextEditors((editors) => {
        if (!lastReport) {
            return;
        }
        for (const editor of editors) {
            decorator.applyTo(editor, lastReport);
        }
    });
    context.subscriptions.push(analyzeCommand, clearCommand, editorChangeListener, decorator);
}
function deactivate() {
    decorator?.dispose();
}
//# sourceMappingURL=extension.js.map