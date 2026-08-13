#pragma once

// =============================================================================
// RAAG — AST Builder
//
// Converts a Tree-sitter parse tree into RAAG's internal arena representation.
//
// This is the boundary where Tree-sitter stops being visible to the rest of
// the system. Everything downstream — the graph builder, the metrics engine —
// consumes AstArena and knows nothing about TSNode. That isolation is what
// makes it possible to add a language, or replace Tree-sitter entirely,
// without touching the analytics layer.
// =============================================================================

#include <tree_sitter/api.h>

#include <string_view>

#include "raag/ast_node.hpp"
#include "raag/tree_sitter_parser.hpp"

namespace raag {

/// Builds an arena from a parsed tree.
///
/// `source_code` must be the exact buffer that produced `root`; node byte
/// offsets index into it, and passing a different buffer yields silently
/// wrong symbol names.
///
/// The walk is iterative rather than recursive. Real-world source files can
/// nest deeply enough — long chains of binary expressions, heavily nested
/// templates — that a recursive walk risks stack exhaustion on input RAAG
/// does not control.
[[nodiscard]] AstArena build_ast(TSNode root,
                                 std::string_view source_code,
                                 Language language);

/// Maps a grammar's node type string onto RAAG's normalized kind.
///
/// Exposed for testing, and deliberately isolated so that adding a language
/// means extending one function rather than hunting through the walker.
[[nodiscard]] AstNodeKind classify_node(std::string_view ts_node_type,
                                        Language language) noexcept;

}  // namespace raag
