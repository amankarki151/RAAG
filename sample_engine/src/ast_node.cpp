#include "raag/ast_node.hpp"

#include <utility>

namespace raag {

std::string_view to_string(AstNodeKind kind) noexcept {
    switch (kind) {
        case AstNodeKind::File:           return "File";
        case AstNodeKind::Class:          return "Class";
        case AstNodeKind::Function:       return "Function";
        case AstNodeKind::Import:         return "Import";
        case AstNodeKind::CallExpression: return "Call";
        case AstNodeKind::Variable:       return "Variable";
        case AstNodeKind::Other:          return "Other";
    }
    // Unreachable for a well-formed enum value. Returned rather than
    // asserted so a corrupt value degrades to visible output instead of
    // terminating a long-running parse.
    return "Unknown";
}

std::uint32_t AstArena::add_node(AstNode node) {
    const auto index = static_cast<std::uint32_t>(nodes_.size());
    nodes_.push_back(std::move(node));
    return index;
}

const AstNode& AstArena::node(std::uint32_t index) const noexcept {
    return nodes_[static_cast<std::size_t>(index)];
}

AstNode& AstArena::node_mut(std::uint32_t index) noexcept {
    return nodes_[static_cast<std::size_t>(index)];
}

std::span<const AstNode> AstArena::children(std::uint32_t index) const noexcept {
    const AstNode& parent = node(index);
    if (parent.child_count == 0) {
        return {};
    }
    return std::span<const AstNode>(
        nodes_.data() + static_cast<std::size_t>(parent.first_child_index),
        static_cast<std::size_t>(parent.child_count));
}

std::span<const AstNode> AstArena::nodes() const noexcept {
    return std::span<const AstNode>(nodes_.data(), nodes_.size());
}

}  // namespace raag
