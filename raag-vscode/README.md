# RAAG for VS Code

Shows a file's coupling and instability right in the editor, computed by [RAAG](https://github.com/amankarki151/RAAG) itself — not reimplemented here.

## What it does

Runs RAAG against your workspace and decorates each analysed file with its measured coupling:

```
Ca=4 Ce=5 I=0.56
```

- **Ca** — how many files depend on this one
- **Ce** — how many files this one depends on
- **I** — instability, `Ce / (Ca + Ce)`, from 0 (stable) to 1 (unstable)

The colour bands match the exact thresholds RAAG's own CI gate uses on its own repository, so what you see in the editor and what CI would flag never disagree.

## Requirements

- [RAAG](https://github.com/amankarki151/RAAG) installed and either on your `PATH` or pointed to via `raag.cliPath`
- A workspace with C++ or Python source RAAG can parse

## Usage

1. Open a folder
2. Run **RAAG: Analyze Workspace** from the Command Palette
3. Open a file — if it crossed a threshold, its metrics show inline

Run **RAAG: Clear Instability Decorations** to remove them.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `raag.cliPath` | `raag` | Where to find the RAAG executable |
| `raag.instabilityWarnThreshold` | `0.4` | Instability at which a file is marked yellow |
| `raag.instabilityErrorThreshold` | `0.7` | Instability at which a file is marked red |
| `raag.minAfferentForWarning` | `3` | Minimum afferent coupling before instability is judged at all |

That last one matters: a file nothing depends on isn't a real finding just because its instability number is high — that's what a leaf module is supposed to look like.

## How it works

This extension doesn't compute anything itself. It shells out to the real `raag` CLI, reads back its metrics JSON, and renders it. If RAAG's analysis changes, this extension's output changes with it automatically — there's no separate logic here that can drift out of sync.

Full architecture, metric definitions, and known limitations are in the [main repository](https://github.com/amankarki151/RAAG).

## License

MIT