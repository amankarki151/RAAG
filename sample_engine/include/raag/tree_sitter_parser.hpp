#pragma once

// =============================================================================
// RAAG — Tree-sitter RAII Wrapper
//
// Tree-sitter is a C library. Its handles (TSParser*, TSTree*) are raw
// pointers that must be released with ts_parser_delete / ts_tree_delete.
// Managing those calls by hand is how leaks and double-frees happen,
// especially on paths that throw.
//
// This class confines all of that to one place, following the "rule of zero":
// ownership is expressed through std::unique_ptr with custom deleters, so no
// destructor is written at all. There is no code path — normal return, early
// return, or exception unwinding — that can skip cleanup, because cleanup is
// the unique_ptr's destructor and destructors always run during unwinding.
// =============================================================================

#include <tree_sitter/api.h>

#include <memory>
#include <string_view>

namespace raag {

/// Source languages RAAG can parse.
enum class Language {
    Cpp,
    Python,
};

/// Returns the Tree-sitter grammar for a language.
[[nodiscard]] const TSLanguage* grammar_for(Language language) noexcept;

namespace detail {

/// Deleters are function objects rather than raw function pointers so that
/// unique_ptr can store them as empty base classes — the smart pointer stays
/// the same size as a raw pointer, costing nothing at runtime.
struct TSParserDeleter {
    void operator()(TSParser* parser) const noexcept {
        if (parser != nullptr) {
            ts_parser_delete(parser);
        }
    }
};

struct TSTreeDeleter {
    void operator()(TSTree* tree) const noexcept {
        if (tree != nullptr) {
            ts_tree_delete(tree);
        }
    }
};

}  // namespace detail

/// Owns a Tree-sitter parser and the tree it most recently produced.
///
/// Move-only. A parser holds mutable internal state, so copying one would
/// either be wrong or silently expensive; deleting the copy operations makes
/// the attempt a compile error.
class TreeSitterParser {
public:
    /// Constructs a parser bound to `language`.
    ///
    /// Throws std::runtime_error if Tree-sitter cannot allocate a parser or
    /// rejects the grammar — the latter indicates a grammar built against an
    /// incompatible Tree-sitter ABI version.
    explicit TreeSitterParser(Language language);

    TreeSitterParser(const TreeSitterParser&) = delete;
    TreeSitterParser& operator=(const TreeSitterParser&) = delete;

    TreeSitterParser(TreeSitterParser&&) noexcept = default;
    TreeSitterParser& operator=(TreeSitterParser&&) noexcept = default;

    ~TreeSitterParser() = default;

    /// Parses `source_code`, replacing any previously parsed tree.
    ///
    /// Returns false if Tree-sitter could not produce a tree at all. Note that
    /// a successful parse does not imply syntactically valid input: Tree-sitter
    /// is error-tolerant by design and will return a tree containing ERROR
    /// nodes for malformed source. That behavior is desirable here, since a
    /// repository under analysis may well contain files that do not compile.
    ///
    /// Reassigning the tree member releases the old tree before storing the
    /// new one, so repeated calls on one instance do not accumulate memory.
    [[nodiscard]] bool parse(std::string_view source_code);

    /// Root of the current tree.
    ///
    /// Throws std::runtime_error if called before a successful parse, rather
    /// than returning a null TSNode that would fail confusingly later.
    [[nodiscard]] TSNode root_node() const;

    /// Whether a tree is currently held.
    [[nodiscard]] bool has_tree() const noexcept { return tree_ != nullptr; }

    /// The language this parser was constructed for.
    [[nodiscard]] Language language() const noexcept { return language_; }

private:
    std::unique_ptr<TSParser, detail::TSParserDeleter> parser_;
    std::unique_ptr<TSTree, detail::TSTreeDeleter> tree_;
    Language language_;
};

}  // namespace raag
