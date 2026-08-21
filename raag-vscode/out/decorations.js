"use strict";
/**
 * Renders instability metrics as inline decorations.
 *
 * Colour bands intentionally mirror `_instability_colour` in the CLI's own
 * `display.py` — green/yellow/red at the same thresholds the violation
 * checks use, so a decoration in the editor and a violation in the terminal
 * never disagree about what counts as concerning.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.InstabilityDecorator = void 0;
const vscode = require("vscode");
function readThresholds() {
    const config = vscode.workspace.getConfiguration("raag");
    return {
        warn: config.get("instabilityWarnThreshold", 0.4),
        error: config.get("instabilityErrorThreshold", 0.7),
        minAfferent: config.get("minAfferentForWarning", 3),
    };
}
/**
 * One decoration type per colour band rather than one dynamic type per file.
 *
 * VS Code decoration types are meant to be created once and reused; creating
 * a fresh `TextEditorDecorationType` per file leaks disposables and is the
 * documented anti-pattern for this API. Three bands is enough granularity
 * for an at-a-glance signal — the exact number is still shown in the label.
 */
class InstabilityDecorator {
    types;
    constructor() {
        this.types = {
            stable: this.makeType("#89d185"),
            warn: this.makeType("#cca700"),
            error: this.makeType("#f14c4c"),
        };
    }
    makeType(colour) {
        return vscode.window.createTextEditorDecorationType({
            after: {
                margin: "0 0 0 1.5em",
                color: colour,
                fontStyle: "italic",
            },
        });
    }
    bandFor(instability, thresholds) {
        if (instability >= thresholds.error)
            return "error";
        if (instability >= thresholds.warn)
            return "warn";
        return "stable";
    }
    /**
     * Applies (or clears) the decoration for one open editor, given the full
     * metrics report. Matching is by workspace-relative path with a leading
     * `./` normalised away, since that is the one path-format subtlety the
     * CLI itself is known to be strict about — see docs/CONTRACTS.md.
     */
    applyTo(editor, report) {
        const workspaceFolder = vscode.workspace.getWorkspaceFolder(editor.document.uri);
        if (!workspaceFolder) {
            return;
        }
        const relativePath = vscode.workspace.asRelativePath(editor.document.uri, false);
        const normalised = relativePath.replace(/^\.\//, "");
        const match = report.modules.find((module) => module.path.replace(/^\.\//, "") === normalised);
        // Clear every band first. A file that no longer matches a violation
        // (because it was fixed, or the report is stale) must not keep showing
        // a decoration from a previous run.
        for (const type of Object.values(this.types)) {
            editor.setDecorations(type, []);
        }
        if (!match) {
            return;
        }
        const thresholds = readThresholds();
        // Below the afferent floor, instability is not a meaningful signal —
        // nothing depends on the file, so nothing breaks if it changes. This
        // mirrors the CLI's own violation gate exactly; decorating every
        // unstable leaf would just be noise the user learns to ignore.
        if (match.afferent_coupling < thresholds.minAfferent) {
            return;
        }
        const band = this.bandFor(match.instability, thresholds);
        const label = `  Ca=${match.afferent_coupling} Ce=${match.efferent_coupling} I=${match.instability.toFixed(2)}`;
        const endOfFirstLine = editor.document.lineAt(0).range.end;
        editor.setDecorations(this.types[band], [
            {
                range: new vscode.Range(endOfFirstLine, endOfFirstLine),
                renderOptions: { after: { contentText: label } },
            },
        ]);
    }
    clear(editor) {
        for (const type of Object.values(this.types)) {
            editor.setDecorations(type, []);
        }
    }
    dispose() {
        for (const type of Object.values(this.types)) {
            type.dispose();
        }
    }
}
exports.InstabilityDecorator = InstabilityDecorator;
//# sourceMappingURL=decorations.js.map