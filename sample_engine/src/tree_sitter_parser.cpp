#include "raag/tree_sitter_parser.hpp"

#include <stdexcept>

// Grammar entry points. Each generated grammar exposes a single C function
// returning its TSLanguage. These are declared here rather than included from
// a header because the grammar repositories ship no public header for them.
extern "C" const TSLanguage* tree_sitter_cpp(void);
extern "C" const TSLanguage* tree_sitter_python(void);

namespace raag {

const TSLanguage* grammar_for(Language language) noexcept {
    switch (language) {
        case Language::Cpp:    return tree_sitter_cpp();
        case Language::Python: return tree_sitter_python();
    }
    return nullptr;
}

TreeSitterParser::TreeSitterParser(Language language)
    : parser_(ts_parser_new()), tree_(nullptr), language_(language) {
    if (!parser_) {
        throw std::runtime_error("Failed to allocate Tree-sitter parser");
    }

    const TSLanguage* grammar = grammar_for(language);
    if (grammar == nullptr) {
        throw std::runtime_error("No grammar available for the requested language");
    }

    // Returns false when the grammar was generated for an incompatible
    // Tree-sitter ABI version. Worth reporting distinctly, because the fix is
    // to align the pinned versions in CMakeLists.txt rather than to change
    // any code here.
    if (!ts_parser_set_language(parser_.get(), grammar)) {
        throw std::runtime_error(
            "Grammar is incompatible with the linked Tree-sitter version");
    }
}

bool TreeSitterParser::parse(std::string_view source_code) {
    // Tree-sitter's length parameter is uint32_t. An explicit cast documents
    // the narrowing rather than leaving it to an implicit conversion, and
    // keeps -Wconversion quiet.
    TSTree* raw_tree = ts_parser_parse_string(
        parser_.get(),
        nullptr,  // No previous tree: this is a full parse, not an incremental one.
        source_code.data(),
        static_cast<std::uint32_t>(source_code.size()));

    if (raw_tree == nullptr) {
        return false;
    }

    // Assignment releases the previously held tree, if any, before taking
    // ownership of the new one. This is what makes repeated parse() calls on
    // a single instance safe.
    tree_.reset(raw_tree);
    return true;
}

TSNode TreeSitterParser::root_node() const {
    if (!tree_) {
        throw std::runtime_error(
            "root_node() called before a successful parse()");
    }
    return ts_tree_root_node(tree_.get());
}

}  // namespace raag
