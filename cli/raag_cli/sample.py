"""The `raag sample` command group.

Wraps the compiled C++ extraction engine. This module does not parse anything
itself — it locates the binary, runs it, and translates its exit status into
something the CLI can report consistently with the Python commands.

Finding the binary is the interesting part. It lives wherever CMake was told
to put it, which varies by build configuration, and a missing binary is by far
the most common first-run failure for anyone cloning the repository. Guessing
one path and reporting "not found" would leave them nowhere; searching the
known locations and naming the build command in the error message means the
fix is in the message itself.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import typer

from raag_cli.display import print_error, print_warning

__all__ = ["app", "find_sample_binary"]

app = typer.Typer(help="Parse source files into binary AST snapshots (C++ engine).")

# Ordered by preference: an optimised build first, since parsing is the
# throughput-bound stage and a Debug binary is several times slower.
_BINARY_LOCATIONS = (
    Path("sample_engine/build-release/raag_sample"),
    Path("sample_engine/build/raag_sample"),
    Path("build-release/raag_sample"),
    Path("build/raag_sample"),
)


def find_sample_binary(project_root: Path | None = None) -> Path | None:
    """Locate the compiled Sample Engine binary.

    Checks the conventional build directories first, then falls back to PATH
    for the case where it has been installed system-wide.
    """
    root = project_root or Path.cwd()

    for candidate in _BINARY_LOCATIONS:
        resolved = root / candidate
        if resolved.is_file():
            return resolved

    on_path = shutil.which("raag_sample")
    return Path(on_path) if on_path else None


def _binary_or_exit(project_root: Path) -> Path:
    binary = find_sample_binary(project_root)

    if binary is None:
        print_error("the Sample Engine binary was not found.")
        print_warning(
            "Build it first:\n"
            "  cmake -S sample_engine -B sample_engine/build-release "
            "-DCMAKE_BUILD_TYPE=Release\n"
            "  cmake --build sample_engine/build-release --parallel"
        )
        raise typer.Exit(code=2)

    if "build-release" not in str(binary):
        print_warning(
            f"using a Debug build ({binary}). Parsing will be substantially "
            f"slower; build with -DCMAKE_BUILD_TYPE=Release for real runs."
        )

    return binary


@app.command("run")
def run(
    path: Path = typer.Argument(..., help="Source file or repository to parse."),
    output: Path = typer.Option(
        Path("snapshots/repo.raag.bin"), "--output", "-o", help="Snapshot destination."
    ),
    threads: int = typer.Option(
        0, "--threads", "-j", help="Worker threads. 0 selects hardware concurrency."
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress per-node output."
    ),
    project_root: Path = typer.Option(
        Path(), "--project-root", help="Directory to search for the binary."
    ),
) -> None:
    """Parse a file or repository into a binary AST snapshot."""
    binary = _binary_or_exit(project_root)

    command = [str(binary), str(path)]
    if output:
        command += ["--output", str(output)]
    if threads > 0:
        command += ["--threads", str(threads)]
    if quiet:
        command.append("--quiet")

    # The C++ engine writes its own progress and summary to stdout. Piping it
    # through unchanged keeps that output intact rather than re-formatting it
    # here and risking the two drifting apart.
    result = subprocess.run(command, check=False)

    if result.returncode != 0:
        raise typer.Exit(code=result.returncode)


@app.command("benchmark")
def benchmark(
    path: Path = typer.Argument(..., help="Repository to benchmark against."),
    project_root: Path = typer.Option(Path(), "--project-root"),
) -> None:
    """Time a single-threaded pass against the parallel one."""
    binary = _binary_or_exit(project_root)

    result = subprocess.run(
        [str(binary), str(path), "--benchmark", "--quiet"], check=False
    )

    if result.returncode != 0:
        raise typer.Exit(code=result.returncode)
