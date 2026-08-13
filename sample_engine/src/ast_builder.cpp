#include "raag/ast_builder.hpp"

#include <cstring>
#include <queue>
#include <utility>

namespace raag {
namespace {

/// Extracts the source text a node spans.
std::string text_of(TSNode node, std::string_view source) {
    const std::uint32_t start = ts_node_start_byte(node);
    const std::uint32_t end = ts_node_end_byte(node);

    if (start >= end || static_cast<std::size_t>(end) > source.size()) {
        return {};
    }

    return std::string(source.substr(static_cast<std::size_t>(start),
                                     static_cast<std::size_t>(end - start)));
}

/// Attempts to read a named field from a node.
///
/// Returns a null TSNode when the field is absent; callers must check with
/// ts_node_is_null before use.
TSNode field(TSNode node, const char* field_name) {
    return ts_node_child_by_field_name(
        node, field_name, static_cast<std::uint32_t>(std::strlen(field_name)));
}

/// Best-effort symbol name for a node.
///
/// APPROXIMATION: grammars disagree on where an identifier lives. Python puts
/// a function's name directly in a "name" field; C++ wraps it in a declarator
/// that may itself nest through pointers and references. This walks the common
/// cases and gives up rather than guessing, because a wrong name is worse than
/// an absent one — it would produce a confidently incorrect dependency edge.
std::string extract_name(TSNode node, std::string_view source) {
    // Direct case: the grammar exposes a "name" field.
    TSNode name_node = field(node, "name");
    if (!ts_node_is_null(name_node)) {
        return text_of(name_node, source);
    }

    // C++ case: descend through declarators to the innermost identifier.
    TSNode declarator = field(node, "declarator");
    int guard = 0;  // Bounds the descent; malformed input should not spin.
    while (!ts_node_is_null(declarator) && guard < 8) {
        const char* type = ts_node_type(declarator);
        if (std::strcmp(type, "identifier") == 0 ||
            std::strcmp(type, "field_identifier") == 0 ||
            std::strcmp(type, "type_identifier") == 0 ||
            std::strcmp(type, "qualified_identifier") == 0 ||
            std::strcmp(type, "operator_name") == 0) {
            return text_of(declarator, source);
        }
        declarator = field(declarator, "declarator");
        ++guard;
    }

    return {};
}

}  // namespace

AstNodeKind classify_node(std::string_view type, Language language) noexcept {
    // Shared across both grammars.
    if (type == "translation_unit" || type == "module") {
        return AstNodeKind::File;
    }
    if (type == "call_expression" || type == "call") {
        return AstNodeKind::CallExpression;
    }

    if (language == Language::Cpp) {
        if (type == "function_definition" || type == "declaration" ||
            type == "template_declaration") {
            // "declaration" covers function prototypes; treating them as
            // functions is intentional, since a prototype still participates
            // in the dependency graph.
            return type == "declaration" ? AstNodeKind::Variable
                                         : AstNodeKind::Function;
        }
        if (type == "class_specifier" || type == "struct_specifier" ||
            type == "union_specifier") {
            return AstNodeKind::Class;
        }
        if (type == "preproc_include" || type == "using_declaration") {
            return AstNodeKind::Import;
        }
        if (type == "field_declaration" || type == "init_declarator" ||
            type == "parameter_declaration") {
            return AstNodeKind::Variable;
        }
    } else {  // Language::Python
        if (type == "function_definition") {
            return AstNodeKind::Function;
        }
        if (type == "class_definition") {
            return AstNodeKind::Class;
        }
        if (type == "import_statement" || type == "import_from_statement" ||
            type == "future_import_statement") {
            return AstNodeKind::Import;
        }
        if (type == "assignment" || type == "parameters") {
            return AstNodeKind::Variable;
        }
    }

    return AstNodeKind::Other;
}

AstArena build_ast(TSNode root, std::string_view source, Language language) {
    AstArena arena;

    if (ts_node_is_null(root)) {
        return arena;
    }

    // Breadth-first, which is what makes the contiguity invariant hold: all
    // children of a node are appended in one uninterrupted batch, so a parent
    // can record them as a single index range. A depth-first walk would
    // interleave grandchildren between siblings and break that.
    struct PendingNode {
        TSNode ts_node;
        std::uint32_t arena_index;
    };

    std::queue<PendingNode> pending;

    AstNode root_record;
    root_record.kind = classify_node(ts_node_type(root), language);
    root_record.name = extract_name(root, source);
    root_record.byte_start = ts_node_start_byte(root);
    root_record.byte_end = ts_node_end_byte(root);
    root_record.parent_index = -1;

    const std::uint32_t root_index = arena.add_node(std::move(root_record));
    pending.push({root, root_index});

    while (!pending.empty()) {
        const PendingNode current = pending.front();
        pending.pop();

        // Named children only. Tree-sitter also exposes anonymous nodes for
        // punctuation and keywords — braces, semicolons, `return` — which
        // carry no structural meaning for architectural analysis and would
        // multiply the node count several-fold for nothing.
        const std::uint32_t child_count = ts_node_named_child_count(current.ts_node);
        if (child_count == 0) {
            continue;
        }

        const auto first_child_index = static_cast<std::uint32_t>(arena.size());

        // Append every child before descending into any of them. This is the
        // step that guarantees contiguity.
        for (std::uint32_t i = 0; i < child_count; ++i) {
            TSNode child = ts_node_named_child(current.ts_node, i);

            AstNode record;
            record.kind = classify_node(ts_node_type(child), language);
            record.name = extract_name(child, source);
            record.byte_start = ts_node_start_byte(child);
            record.byte_end = ts_node_end_byte(child);
            record.parent_index = static_cast<std::int32_t>(current.arena_index);

            const std::uint32_t child_arena_index = arena.add_node(std::move(record));
            pending.push({child, child_arena_index});
        }

        // Backfill the parent's range now that the children are placed. The
        // parent reference is taken after the appends because push_back may
        // reallocate, invalidating any reference held across it.
        AstNode& parent = arena.node_mut(current.arena_index);
        parent.first_child_index = first_child_index;
        parent.child_count = child_count;
    }

    return arena;
}

}  // namespace raag
