#pragma once

// =============================================================================
// RAAG — Repository traversal
//
// Enumerates the source files in a repository. Deliberately separate from the
// parser so the two can be tested independently, and so the extension-to-
// language mapping lives in exactly one place rather than being duplicated
// between the CLI and the walker.
// =============================================================================

#include <filesystem>
#include <optional>
#include <vector>

#include "raag/tree_sitter_parser.hpp"

namespace raag {

/// Maps a file extension onto a supported grammar.
///
/// Returns nullopt for anything unrecognized rather than guessing. Parsing a
/// file with the wrong grammar yields a tree full of ERROR nodes and nonsense
/// structure, which is far harder to diagnose than an upfront refusal.
[[nodiscard]] std::optional<Language> language_for_extension(
    const std::filesystem::path& path);

/// Recursively collects every parseable source file under `root`.
///
/// Directories that hold generated output or third-party code — build trees,
/// virtual environments, dependency caches, version control metadata — are
/// skipped. Including them would inflate parse counts with code the repository
/// does not own, and distort every metric computed downstream.
///
/// Returns an empty vector if `root` does not exist or is not a directory.
[[nodiscard]] std::vector<std::filesystem::path> collect_source_files(
    const std::filesystem::path& root);

/// Whether a directory should be skipped during traversal.
///
/// Exposed for testing; the traversal calls it internally.
[[nodiscard]] bool is_excluded_directory(const std::filesystem::path& directory_name);

}  // namespace raag