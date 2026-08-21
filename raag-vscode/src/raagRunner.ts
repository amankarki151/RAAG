/**
 * Runs the real `raag` CLI as a subprocess and reads back its JSON export.
 *
 * Deliberately thin. This module owns exactly one responsibility: turn a
 * workspace folder into a metrics report. It does not parse coupling, walk a
 * dependency graph, or compute instability — that logic already exists,
 * tested, in the Python CLI, and reimplementing it here would create a
 * second implementation that can silently drift from the first.
 */

import * as vscode from "vscode";
import * as cp from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

export interface ModuleMetrics {
  path: string;
  afferent_coupling: number;
  efferent_coupling: number;
  instability: number;
}

export interface MetricsReport {
  modules: ModuleMetrics[];
  summary: Record<string, unknown>;
}

export class RaagCliError extends Error {
  constructor(
    message: string,
    public readonly stderr: string,
  ) {
    super(message);
    this.name = "RaagCliError";
  }
}

function run(command: string, args: string[], cwd: string): Promise<{ stdout: string; stderr: string; code: number }> {
  return new Promise((resolve, reject) => {
    const child = cp.spawn(command, args, { cwd });

    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => (stdout += chunk.toString()));
    child.stderr.on("data", (chunk) => (stderr += chunk.toString()));

    // ENOENT here specifically means the binary itself was not found — a
    // different failure mode from the command running and exiting non-zero,
    // and one the caller needs to distinguish to give an actionable message
    // ("raag is not installed or not on PATH" vs. "raag found a violation").
    child.on("error", (error) => reject(error));

    child.on("close", (code) => resolve({ stdout, stderr, code: code ?? 1 }));
  });
}

/**
 * Parses, then analyzes, a workspace folder end to end.
 *
 * Two real CLI invocations, mirroring exactly what a user would type by
 * hand: `raag sample run` to produce a snapshot, `raag tune run
 * --export-metrics` to compute the report. Both write to a temp directory
 * unique to this call, so concurrent analyses (or a stale run from a crashed
 * previous session) never collide.
 */
export async function analyzeWorkspace(
  workspaceFolder: string,
  cliPath: string,
): Promise<MetricsReport> {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "raag-vscode-"));
  const snapshotPath = path.join(tempDir, "workspace.raag.bin");
  const metricsPath = path.join(tempDir, "metrics.json");

  try {
    const sampleResult = await run(
      cliPath,
      ["sample", "run", ".", "--output", snapshotPath, "--quiet"],
      workspaceFolder,
    );

    if (sampleResult.code === 2) {
      // Exit code 2 from the Sample Engine step is almost always the binary
      // itself missing — the CLI's own error message already names the
      // cmake command that fixes it, so it is surfaced verbatim rather than
      // re-worded here.
      throw new RaagCliError(
        "raag sample run failed. Is the Sample Engine built?",
        sampleResult.stderr,
      );
    }

    const tuneResult = await run(
      cliPath,
      ["tune", "run", snapshotPath, "--export-metrics", metricsPath],
      workspaceFolder,
    );

    // Exit code 1 here means real violations were found — that is success
    // from this function's point of view, not a failure. Only exit code 2
    // (could not run at all) is treated as an error.
    if (tuneResult.code === 2) {
      throw new RaagCliError("raag tune run could not analyze the workspace.", tuneResult.stderr);
    }

    if (!fs.existsSync(metricsPath)) {
      throw new RaagCliError(
        "raag ran but did not produce a metrics report.",
        tuneResult.stderr,
      );
    }

    const raw = fs.readFileSync(metricsPath, "utf-8");
    return JSON.parse(raw) as MetricsReport;
  } catch (error) {
    if (error instanceof RaagCliError) {
      throw error;
    }
    // A raw ENOENT from spawn means the raag executable itself was not
    // found on PATH, which is a configuration problem, not an analysis
    // failure — worth a distinct, actionable message.
    const nodeError = error as NodeJS.ErrnoException;
    if (nodeError.code === "ENOENT") {
      throw new RaagCliError(
        `Could not find '${cliPath}'. Set raag.cliPath in settings, or activate the virtual environment RAAG is installed in before launching VS Code.`,
        "",
      );
    }
    throw error;
  } finally {
    // Best-effort cleanup. A leftover temp directory from a failed run is
    // not worth failing the whole analysis over.
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
}

export function getConfiguredCliPath(): string {
  return vscode.workspace.getConfiguration("raag").get<string>("cliPath", "raag");
}