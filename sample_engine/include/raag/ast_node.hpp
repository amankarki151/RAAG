#pragma once

// =============================================================================
// RAAG — Internal AST Representation
//
// Tree-sitter's own tree is not used beyond the extraction boundary. This
// header defines RAAG's internal, language-agnostic representation instead.
//
// DESIGN: arena-based flat storage.
//
// Nodes live in a single contiguous std::vector. A node refers to its children
// by an index range rather than owning pointers to them. This is chosen over a
// pointer-linked tree for three reasons:
//
//   1. Cache locality — a traversal walks memory linearly instead of chasing
//      pointers scattered across the heap.
//   2. One allocation amortized across the whole tree, rather than one
//      allocation per node.
//   3. Trivial ownership — the vector owns everything, so there is no
//      destructor to write and no chance of a leak or double-free.
//
// The invariant that makes the index range work: a node's children are always
// stored contiguously. The builder guarantees this by appending all children
// of a node in a single batch (see ast_builder.cpp).
// =============================================================================

#include <cstdint>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace raag {

/// Structural classification of a node, normalized across languages.
///
/// Deliberately coarse: RAAG reasons about architecture, not syntax, so it
/// needs to know that something is a function without caring whether the
/// grammar called it `function_definition` or `function_declaration`.
enum class AstNodeKind : std::uint8_t {
    File,
    Class,
    Function,
    Import,
    CallExpression,
    Variable,
    Other,
};

/// Human-readable name for a node kind, for diagnostics and CLI output.
[[nodiscard]] std::string_view to_string(AstNodeKind kind) noexcept;

/// A single node in the arena.
///
/// Trivially copyable apart from `name`. Kept small so a traversal touches as
/// few cache lines as possible.
struct AstNode {
    /// Normalized structural kind.
    AstNodeKind kind{AstNodeKind::Other};

    /// Symbol name where one exists (function name, class name). Empty
    /// otherwise — many nodes are structural and carry no identifier.
    std::string name;

    /// Byte offset of the node's first character in the source file.
    std::uint32_t byte_start{0};

    /// Byte offset one past the node's last character.
    std::uint32_t byte_end{0};

    /// Index of this node's first child in the arena. Meaningless when
    /// `child_count` is zero.
    std::uint32_t first_child_index{0};

    /// Number of children, stored contiguously from `first_child_index`.
    std::uint32_t child_count{0};

    /// Index of this node's parent, or -1 for the root.
    std::int32_t parent_index{-1};
};

/// Owning container for a parsed file's nodes.
///
/// Move-only: an arena can be large, and copying one is almost always a
/// mistake rather than an intent. Deleting the copy operations makes that
/// mistake a compile error instead of a silent performance problem.
class AstArena {
public:
    AstArena() = default;

    AstArena(const AstArena&) = delete;
    AstArena& operator=(const AstArena&) = delete;

    AstArena(AstArena&&) noexcept = default;
    AstArena& operator=(AstArena&&) noexcept = default;

    ~AstArena() = default;

    /// Appends a node and returns its index.
    ///
    /// The returned index is stable for the lifetime of the arena: nodes are
    /// never reordered or removed, so an index recorded now stays valid.
    std::uint32_t add_node(AstNode node);

    /// Read-only access to a node by index. Caller must ensure `index` is
    /// valid; bounds are not checked on the hot path.
    [[nodiscard]] const AstNode& node(std::uint32_t index) const noexcept;

    /// Mutable access, used by the builder to backfill child ranges once a
    /// node's children have been appended.
    [[nodiscard]] AstNode& node_mut(std::uint32_t index) noexcept;

    /// The children of `index` as a contiguous view.
    ///
    /// Returns an empty span for a leaf. The contiguity invariant documented
    /// at the top of this file is what makes this a view rather than a copy.
    [[nodiscard]] std::span<const AstNode> children(std::uint32_t index) const noexcept;

    /// All nodes in insertion order. Useful for linear passes that do not
    /// care about tree structure, such as counting nodes by kind.
    [[nodiscard]] std::span<const AstNode> nodes() const noexcept;

    [[nodiscard]] std::size_t size() const noexcept { return nodes_.size(); }
    [[nodiscard]] bool empty() const noexcept { return nodes_.empty(); }

    /// Hint to the allocator when the approximate node count is known ahead of
    /// time, avoiding repeated reallocation during a large parse.
    void reserve(std::size_t capacity) { nodes_.reserve(capacity); }

private:
    std::vector<AstNode> nodes_;
};

}  // namespace raag
