"""Tests for the command-line interface.

Typer's CliRunner invokes commands in-process, so these exercise real argument
parsing and real exit codes without spawning subprocesses.

Exit codes get the most attention here. Day 9's CI gate decides whether to
block a merge based entirely on what `raag tune run --fail-on-instability`
returns, which makes the mapping from findings to exit status an interface
contract rather than an implementation detail.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from typer.testing import CliRunner

from raag_cli.main import app
from raag_cli.sample import find_sample_binary

runner = CliRunner()


# --- Snapshot fixtures -------------------------------------------------------


def encode_string(value: str) -> bytes:
    data = value.encode("utf-8")
    return struct.pack("<I", len(data)) + data


def encode_node(
    kind: int,
    name: str,
    start: int,
    end: int,
    first_child: int,
    count: int,
    parent: int,
) -> bytes:
    return (
        struct.pack("<B", kind)
        + encode_string(name)
        + struct.pack("<IIIi", start, end, first_child, count)[:12]
        + struct.pack("<I", count)
        + struct.pack("<i", parent)
    )


def make_snapshot(tmp_path: Path) -> Path:
    """A minimal but structurally valid two-file snapshot.

    Written by hand rather than produced by the C++ engine so CLI tests run in
    CI where that binary may not have been built.
    """

    def node_record(kind, name, start, end, first_child, count, parent):
        return (
            struct.pack("<B", kind)
            + encode_string(name)
            + struct.pack("<I", start)
            + struct.pack("<I", end)
            + struct.pack("<I", first_child)
            + struct.pack("<I", count)
            + struct.pack("<i", parent)
        )

    def file_record(path: str, nodes: list[bytes]) -> bytes:
        return encode_string(path) + struct.pack("<I", len(nodes)) + b"".join(nodes)

    app_nodes = [
        node_record(0, "", 0, 100, 1, 1, -1),
        node_record(3, "core.hpp", 0, 20, 0, 0, 0),
    ]
    core_nodes = [node_record(0, "", 0, 50, 0, 0, -1)]

    payload = (
        b"RAAG"
        + struct.pack("<I", 2)
        + struct.pack("<I", 2)
        + file_record("app.cpp", app_nodes)
        + file_record("core.hpp", core_nodes)
    )

    destination = tmp_path / "test.raag.bin"
    destination.write_bytes(payload)
    return destination


# --- Top-level ---------------------------------------------------------------


def test_bare_invocation_shows_help():
    """no_args_is_help means a user who types `raag` gets guidance, not an
    empty prompt or a stack trace."""
    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "sample" in result.stdout
    assert "tune" in result.stdout
    assert "master" in result.stdout


def test_help_flag():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "architectural analysis" in result.stdout.lower()


def test_version_command():
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert "raag" in result.stdout.lower()


def test_unknown_command_fails_cleanly():
    result = runner.invoke(app, ["nonsense"])

    assert result.exit_code != 0


@pytest.mark.parametrize("group", ["sample", "tune", "master"])
def test_each_group_has_help(group):
    result = runner.invoke(app, [group, "--help"])

    assert result.exit_code == 0


# --- Missing inputs ----------------------------------------------------------


def test_tune_on_missing_snapshot_exits_two(tmp_path):
    """Exit 2 means 'could not run', distinct from exit 1 'ran and found
    violations'. The CI gate needs to tell those apart — a missing file is a
    setup error, not an architectural finding."""
    result = runner.invoke(app, ["tune", "run", str(tmp_path / "absent.raag.bin")])

    assert result.exit_code == 2


def test_tune_on_corrupt_snapshot_exits_two(tmp_path):
    corrupt = tmp_path / "corrupt.raag.bin"
    corrupt.write_bytes(b"NOPE" + b"\x00" * 32)

    result = runner.invoke(app, ["tune", "run", str(corrupt)])

    assert result.exit_code == 2


def test_master_index_on_missing_snapshot_exits_two(tmp_path):
    result = runner.invoke(
        app, ["master", "index", str(tmp_path / "absent.raag.bin"), "--offline"]
    )

    assert result.exit_code == 2


# --- tune ---------------------------------------------------------------------


def test_tune_run_succeeds_on_a_clean_snapshot(tmp_path):
    result = runner.invoke(app, ["tune", "run", str(make_snapshot(tmp_path))])

    assert result.exit_code == 0
    assert "Dependency graph" in result.stdout


def test_tune_exports_metrics_json(tmp_path):
    destination = tmp_path / "out" / "metrics.json"

    result = runner.invoke(
        app,
        [
            "tune",
            "run",
            str(make_snapshot(tmp_path)),
            "--export-metrics",
            str(destination),
        ],
    )

    assert result.exit_code == 0
    assert destination.exists()


def test_tune_exports_graph_json(tmp_path):
    destination = tmp_path / "out" / "graph.json"

    result = runner.invoke(
        app,
        [
            "tune",
            "run",
            str(make_snapshot(tmp_path)),
            "--export-graph",
            str(destination),
        ],
    )

    assert result.exit_code == 0
    assert destination.exists()


def test_graph_export_is_byte_stable(tmp_path):
    """Two runs over unchanged code must produce identical files, or a metrics
    diff between commits is unreadable noise."""
    snapshot = make_snapshot(tmp_path)
    first = tmp_path / "one.json"
    second = tmp_path / "two.json"

    runner.invoke(app, ["tune", "run", str(snapshot), "--export-graph", str(first)])
    runner.invoke(app, ["tune", "run", str(snapshot), "--export-graph", str(second)])

    assert first.read_text() == second.read_text()


def test_fail_on_instability_passes_when_clean(tmp_path):
    result = runner.invoke(
        app, ["tune", "run", str(make_snapshot(tmp_path)), "--fail-on-instability"]
    )

    assert result.exit_code == 0


def test_thresholds_are_accepted_from_the_command_line(tmp_path):
    """Thresholds are policy. A tool that hard-codes them is wrong about half
    the repositories it sees."""
    result = runner.invoke(
        app,
        [
            "tune",
            "run",
            str(make_snapshot(tmp_path)),
            "--max-instability",
            "0.9",
            "--min-afferent",
            "10",
        ],
    )

    assert result.exit_code == 0


def test_cycles_command_reports_none_for_an_acyclic_graph(tmp_path):
    result = runner.invoke(app, ["tune", "cycles", str(make_snapshot(tmp_path))])

    assert result.exit_code == 0
    assert "No dependency cycles" in result.stdout


def test_show_layers_prints_layering(tmp_path):
    result = runner.invoke(
        app, ["tune", "run", str(make_snapshot(tmp_path)), "--show-layers"]
    )

    assert result.exit_code == 0
    assert "Layer 0" in result.stdout


# --- sample -------------------------------------------------------------------


def test_sample_reports_a_missing_binary_with_the_build_command(tmp_path):
    """A missing binary is the most common first-run failure. The error has to
    carry the fix, or a new contributor is left with nowhere to go."""
    result = runner.invoke(
        app,
        ["sample", "run", str(tmp_path / "src"), "--project-root", str(tmp_path)],
    )

    assert result.exit_code == 2
    assert "cmake" in result.output.lower()


def test_find_sample_binary_returns_none_when_absent(tmp_path):
    assert find_sample_binary(tmp_path) is None


def test_find_sample_binary_prefers_the_release_build(tmp_path):
    """Parsing is throughput-bound; a Debug binary is several times slower."""
    for directory in ("build", "build-release"):
        target = tmp_path / "sample_engine" / directory
        target.mkdir(parents=True)
        (target / "raag_sample").write_text("#!/bin/sh\n")

    found = find_sample_binary(tmp_path)

    assert found is not None
    assert "build-release" in str(found)


# --- master -------------------------------------------------------------------


def test_master_refactor_rejects_an_unknown_target(tmp_path):
    """A target absent from the graph means the question was wrong, not that
    the change is safe. It must not be reported as an empty blast radius."""
    result = runner.invoke(
        app,
        [
            "master",
            "refactor",
            "nowhere/missing.hpp",
            "reduce coupling",
            "--snapshot",
            str(make_snapshot(tmp_path)),
            "--dry-run",
            "--no-audit",
            "--offline",
        ],
    )

    assert result.exit_code == 2


def test_master_audit_on_an_empty_log(tmp_path):
    result = runner.invoke(
        app, ["master", "audit", "--audit-db", str(tmp_path / "a.db")]
    )

    assert result.exit_code == 0
    assert "No audit records" in result.stdout


def test_master_audit_rejects_an_unknown_record(tmp_path):
    result = runner.invoke(
        app,
        ["master", "audit", "--record-id", "999", "--audit-db", str(tmp_path / "a.db")],
    )

    assert result.exit_code == 2
