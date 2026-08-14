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

bool type_is(TSNode node, const char* expected) {
    return std::strcmp(ts_node_type(node), expected) == 0;
}

/// Strips the delimiters from a C++ include path.
///
/// The grammar returns the target with its delimiters attached — either
/// <vector> or "local.hpp". Both are stripped to bare text. The system/local
/// distinction is not preserved because it does not survive across projects
/// anyway: a header on the include path in one build is vendored in the next.
/// Resolution downstream is by lookup against the actual file set instead.
std::string strip_include_delimiters(std::string text) {
    if (text.size() < 2) {
        return text;
    }

    const char first = text.front();
    const char last = text.back();

    const bool angled = (first == '<' && last == '>');
    const bool quoted = (first == '"' && last == '"');

    if (angled || quoted) {
        return text.substr(1, text.size() - 2);
    }

    return text;
}

/// Extracts the field name from a member access expression.
///
/// Only accesses through the enclosing instance count — `self.x` in Python,
/// `this->x` in C++. An access through some other object is a dependency on
/// that object, not a use of this class's own state, and counting it would
/// make every class look cohesive regardless of how its methods actually
/// relate.
std::string extract_field_access(TSNode node, std::string_view source, Language language) {
    if (language == Language::Cpp) {
        TSNode argument = field(node, "argument");
        if (ts_node_is_null(argument) || !type_is(argument, "this")) {
            return {};
        }
        TSNode member = field(node, "field");
        return ts_node_is_null(member) ? std::string{} : text_of(member, source);
    }

    TSNode object = field(node, "object");
    if (ts_node_is_null(object) || text_of(object, source) != "self") {
        return {};
    }
    TSNode attribute = field(node, "attribute");
    return ts_node_is_null(attribute) ? std::string{} : text_of(attribute, source);
}


/// Extracts what an import node refers to.
///
/// Import targets live in different fields per grammar, and none of them is the
/// "name" field the general extractor looks for. Without this the snapshot
/// records that a file has imports but not what they import — which is exactly
/// the information the dependency graph is built from.
std::string extract_import_target(TSNode node,
                                  std::string_view source,
                                  Language language) {
    if (language == Language::Cpp) {
        // #include <vector> / #include "local.hpp"
        TSNode path_node = field(node, "path");
        if (!ts_node_is_null(path_node)) {
            return strip_include_delimiters(text_of(path_node, source));
        }

        // using namespace foo; / using foo::bar;
        // The whole declaration minus the keyword: the grammar exposes no
        // single field covering the qualified name.
        std::string text = text_of(node, source);
        constexpr std::string_view kUsing = "using ";
        if (text.rfind(kUsing, 0) == 0) {
            text.erase(0, kUsing.size());
        }
        if (!text.empty() && text.back() == ';') {
            text.pop_back();
        }
        return text;
    }

    // Python.
    //
    //   import os                  -> "name" holds os
    //   import os.path as p        -> "name" holds an aliased_import
    //   from pathlib import Path   -> "module_name" holds pathlib
    //   from . import sibling      -> "module_name" holds a relative_import
    TSNode module_name = field(node, "module_name");
    if (!ts_node_is_null(module_name)) {
        return text_of(module_name, source);
    }

    TSNode name_node = field(node, "name");
    if (!ts_node_is_null(name_node)) {
        // An aliased import wraps the real target one level deeper.
        if (type_is(name_node, "aliased_import")) {
            TSNode inner = field(name_node, "name");
            if (!ts_node_is_null(inner)) {
                return text_of(inner, source);
            }
        }
        return text_of(name_node, source);
    }

    return {};
}

/// Extracts the base types a class declaration inherits from, comma-joined.
///
/// The snapshot format carries one string per node, and widening it for a case
/// this narrow is not worth a schema break; the graph layer splits on the
/// separator. Recorded here rather than derived later because the structural
/// position of a base clause is grammar-specific, and grammar knowledge belongs
/// on this side of the boundary.
std::string extract_base_types(TSNode node, std::string_view source, Language language) {
    const char* clause_type =
        (language == Language::Cpp) ? "base_class_clause" : "argument_list";

    const std::uint32_t child_count = ts_node_named_child_count(node);
    std::string joined;

    for (std::uint32_t i = 0; i < child_count; ++i) {
        TSNode child = ts_node_named_child(node, i);
        if (!type_is(child, clause_type)) {
            continue;
        }

        const std::uint32_t base_count = ts_node_named_child_count(child);
        for (std::uint32_t j = 0; j < base_count; ++j) {
            std::string base = text_of(ts_node_named_child(child, j), source);
            if (base.empty()) {
                continue;
            }
            if (!joined.empty()) {
                joined += ',';
            }
            joined += base;
        }
        break;
    }

    return joined;
}

/// Best-effort symbol name for a node.
///
/// APPROXIMATION: grammars disagree on where an identifier lives. Python puts a
/// function's name directly in a "name" field; C++ wraps it in a declarator that
/// may itself nest through pointers and references. This walks the common cases
/// and gives up rather than guessing, because a wrong name is worse than an
/// absent one — it would produce a confidently incorrect dependency edge.
std::string extract_name(TSNode node,
                         std::string_view source,
                         AstNodeKind kind,
                         Language language) {
    if (kind == AstNodeKind::Import) {
        return extract_import_target(node, source, language);
    }
        if (kind == AstNodeKind::FieldAccess) {
        return extract_field_access(node, source, language);
    }


    // Direct case: the grammar exposes a "name" field.
    TSNode name_node = field(node, "name");
    if (!ts_node_is_null(name_node)) {
        std::string name = text_of(name_node, source);

        // A class carries its base types alongside its own name so the graph
        // layer can record inheritance edges without re-parsing.
        if (kind == AstNodeKind::Class) {
            const std::string bases = extract_base_types(node, source, language);
            if (!bases.empty()) {
                name += " : " + bases;
            }
        }
        return name;
    }

    // Call expressions name their callee in a "function" field.
    if (kind == AstNodeKind::CallExpression) {
        TSNode callee = field(node, "function");
        if (!ts_node_is_null(callee)) {
            return text_of(callee, source);
        }
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

        TSNode inner = field(declarator, "declarator");
        if (ts_node_is_null(inner)) {
            break;
        }
        declarator = inner;
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
        
        if (type == "function_definition" || type == "template_declaration") {
            return AstNodeKind::Function;
        }
        if (type == "field_expression") {
            return AstNodeKind::FieldAccess;
        }

        if (type == "class_specifier" || type == "struct_specifier" ||
            type == "union_specifier") {
            return AstNodeKind::Class;
        }
        if (type == "preproc_include" || type == "using_declaration") {
            return AstNodeKind::Import;
        }
        if (type == "declaration" || type == "field_declaration" ||
            type == "init_declarator" || type == "parameter_declaration") {
            return AstNodeKind::Variable;
        }
    } else {  // Language::Python
        if (type == "function_definition") {
            return AstNodeKind::Function;
        }
        if (type == "attribute") {
            return AstNodeKind::FieldAccess;
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
    root_record.name = extract_name(root, source, root_record.kind, language);
    root_record.byte_start = ts_node_start_byte(root);
    root_record.byte_end = ts_node_end_byte(root);
    root_record.parent_index = -1;

    const std::uint32_t root_index = arena.add_node(std::move(root_record));
    pending.push({root, root_index});

    while (!pending.empty()) {
        const PendingNode current = pending.front();
        pending.pop();

        // Named children only. Tree-sitter also exposes anonymous nodes for
        // punctuation and keywords — braces, semicolons, `return` — which carry
        // no structural meaning for architectural analysis and would multiply
        // the node count several-fold for nothing.
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
            record.name = extract_name(child, source, record.kind, language);
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