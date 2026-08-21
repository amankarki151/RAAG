/**
 * Renders instability metrics as inline decorations.
 *
 * Colour bands intentionally mirror `_instability_colour` in the CLI's own
 * `display.py` — green/yellow/red at the same thresholds the violation
 * checks use, so a decoration in the editor and a violation in the terminal
 * never disagree about what counts as concerning.
 */

import * as vscode from "vscode";
import { ModuleMetrics } from "./raagRunner";

interface Thresholds {
  warn: number;
  error: number;
  minAfferent: number;
}

function readThresholds(): Thresholds {
  const config = vscode.workspace.getConfiguration("raag");
  return {
    warn: config.get<number>("instabilityWarnThreshold", 0.4),
    error: config.get<number>("instabilityErrorThreshold", 0.7),
    minAfferent: config.get<number>("minAfferentForWarning", 3),
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
export class InstabilityDecorator {
  private readonly types: Record<"stable" | "warn" | "error", vscode.TextEditorDecorationType>;

  constructor() {
    this.types = {
      stable: this.makeType("#89d185"),
      warn: this.makeType("#cca700"),
      error: this.makeType("#f14c4c"),
    };
  }

  private makeType(colour: string): vscode.TextEditorDecorationType {
    return vscode.window.createTextEditorDecorationType({
      after: {
        margin: "0 0 0 1.5em",
        color: colour,
        fontStyle: "italic",
      },
    });
  }

  private bandFor(instability: number, thresholds: Thresholds): "stable" | "warn" | "error" {
    if (instability >= thresholds.error) return "error";
    if (instability >= thresholds.warn) return "warn";
    return "stable";
  }

  /**
   * Applies (or clears) the decoration for one open editor, given the full
   * metrics report. Matching is by workspace-relative path with a leading
   * `./` normalised away, since that is the one path-format subtlety the
   * CLI itself is known to be strict about — see docs/CONTRACTS.md.
   */
  public applyTo(editor: vscode.TextEditor, report: { modules: ModuleMetrics[] }): void {
    const workspaceFolder = vscode.workspace.getWorkspaceFolder(editor.document.uri);
    if (!workspaceFolder) {
      return;
    }

    const relativePath = vscode.workspace.asRelativePath(editor.document.uri, false);
    const normalised = relativePath.replace(/^\.\//, "");

    const match = report.modules.find(
      (module) => module.path.replace(/^\.\//, "") === normalised,
    );

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

  public clear(editor: vscode.TextEditor): void {
    for (const type of Object.values(this.types)) {
      editor.setDecorations(type, []);
    }
  }

  public dispose(): void {
    for (const type of Object.values(this.types)) {
      type.dispose();
    }
  }
}