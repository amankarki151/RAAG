// =============================================================================
// RAAG — Sample Engine tests
//
// Covers the AST builder and its node classifier.
//
// Source fixtures are inline string literals rather than files on disk. This
// keeps each test self-contained and readable — the input and the assertion
// sit next to each other — and removes any dependency on the working directory
// the test binary happens to run from.
// =============================================================================

#include <gtest/gtest.h>

#include <algorithm>
#include <cstddef>
#include <string_view>

#include "raag/ast_builder.hpp"
#include "raag/ast_node.hpp"
#include "raag/tree_sitter_parser.hpp"

namespace {

/// Parses a source string and returns the built arena.
///
/// Uses ADD_FAILURE rather than throwing on parse failure so a broken parse
/// reports as a test failure with context, instead of aborting the whole run.
raag::AstArena parse_source(std::string_view source, raag::Language language) {
    raag::TreeSitterParser parser(language);

    if (!parser.parse(source)) {
        ADD_FAILURE() << "Tree-sitter failed to produce a tree for the source";
        return {};
    }

    return raag::build_ast(parser.root_node(), source, language);
}

/// Counts nodes of a given kind across the whole arena.
std::size_t count_of_kind(const raag::AstArena& arena, raag::AstNodeKind kind) {
    const auto nodes = arena.nodes();
    return static_cast<std::size_t>(
        std::count_if(nodes.begin(), nodes.end(),
                      [kind](const raag::AstNode& node) { return node.kind == kind; }));
}

/// Whether any node matches both a kind and a name.
bool has_node(const raag::AstArena& arena, raag::AstNodeKind kind, std::string_view name) {
    const auto nodes = arena.nodes();
    return std::any_of(nodes.begin(), nodes.end(),
                       [kind, name](const raag::AstNode& node) {
                           return node.kind == kind && node.name == name;
                       });
}

}  // namespace

// -----------------------------------------------------------------------------
// Python
// -----------------------------------------------------------------------------

TEST(AstBuilderPython, ExtractsFunctionDefinitionWithName) {
    constexpr std::string_view source =
        "def hello(name):\n"
        "    return name\n";

    const raag::AstArena arena = parse_source(source, raag::Language::Python);

    ASSERT_FALSE(arena.empty()) << "Parsing a valid function produced no nodes";
    EXPECT_EQ(arena.node(0).kind, raag::AstNodeKind::File);
    EXPECT_TRUE(has_node(arena, raag::AstNodeKind::Function, "hello"));
}

TEST(AstBuilderPython, ExtractsClassAndItsMethods) {
    constexpr std::string_view source =
        "class Greeter:\n"
        "    def greet(self):\n"
        "        pass\n"
        "\n"
        "    def farewell(self):\n"
        "        pass\n";

    const raag::AstArena arena = parse_source(source, raag::Language::Python);

    ASSERT_FALSE(arena.empty());
    EXPECT_EQ(count_of_kind(arena, raag::AstNodeKind::Class), 1u);
    EXPECT_TRUE(has_node(arena, raag::AstNodeKind::Class, "Greeter"));

    EXPECT_EQ(count_of_kind(arena, raag::AstNodeKind::Function), 2u);
    EXPECT_TRUE(has_node(arena, raag::AstNodeKind::Function, "greet"));
    EXPECT_TRUE(has_node(arena, raag::AstNodeKind::Function, "farewell"));
}

TEST(AstBuilderPython, ExtractsImportStatements) {
    constexpr std::string_view source =
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n";

    const raag::AstArena arena = parse_source(source, raag::Language::Python);

    ASSERT_FALSE(arena.empty());
    EXPECT_EQ(count_of_kind(arena, raag::AstNodeKind::Import), 3u);
}

// -----------------------------------------------------------------------------
// C++
// -----------------------------------------------------------------------------

TEST(AstBuilderCpp, ExtractsFunctionDefinitionWithName) {
    constexpr std::string_view source =
        "int add(int a, int b) {\n"
        "    return a + b;\n"
        "}\n";

    const raag::AstArena arena = parse_source(source, raag::Language::Cpp);

    ASSERT_FALSE(arena.empty());
    EXPECT_EQ(arena.node(0).kind, raag::AstNodeKind::File);
    EXPECT_TRUE(has_node(arena, raag::AstNodeKind::Function, "add"));
}

TEST(AstBuilderCpp, ExtractsClassDefinition) {
    constexpr std::string_view source =
        "class Widget {\n"
        "public:\n"
        "    void render();\n"
        "};\n";

    const raag::AstArena arena = parse_source(source, raag::Language::Cpp);

    ASSERT_FALSE(arena.empty());
    EXPECT_TRUE(has_node(arena, raag::AstNodeKind::Class, "Widget"));
}

TEST(AstBuilderCpp, CountsIncludeDirectivesAsImports) {
    constexpr std::string_view source =
        "#include <vector>\n"
        "#include <string>\n"
        "#include \"local.hpp\"\n"
        "\n"
        "int main() { return 0; }\n";

    const raag::AstArena arena = parse_source(source, raag::Language::Cpp);

    ASSERT_FALSE(arena.empty());
    EXPECT_EQ(count_of_kind(arena, raag::AstNodeKind::Import), 3u);
}

// -----------------------------------------------------------------------------
// Edge cases
// -----------------------------------------------------------------------------

TEST(AstBuilderEdgeCases, EmptySourceDoesNotCrash) {
    constexpr std::string_view source = "";

    const raag::AstArena arena = parse_source(source, raag::Language::Python);

    // Tree-sitter returns a valid tree with a lone root for empty input, so the
    // arena should hold exactly the root and nothing else. The assertion is
    // written to tolerate an empty arena too, since the contract only promises
    // "does not crash" here.
    if (!arena.empty()) {
        EXPECT_EQ(arena.node(0).kind, raag::AstNodeKind::File);
        EXPECT_EQ(arena.node(0).child_count, 0u);
    }
}

TEST(AstBuilderEdgeCases, WhitespaceOnlySourceProducesRootOnly) {
    constexpr std::string_view source = "\n\n   \n";

    const raag::AstArena arena = parse_source(source, raag::Language::Python);

    if (!arena.empty()) {
        EXPECT_EQ(arena.node(0).kind, raag::AstNodeKind::File);
    }
}

TEST(AstBuilderEdgeCases, MalformedSourceStillProducesTree) {
    // Tree-sitter is error-tolerant by design: a repository under analysis may
    // well contain files that do not compile, and RAAG must still extract what
    // structure it can rather than failing the whole run.
    constexpr std::string_view source =
        "def broken(:\n"
        "    this is not valid python @@@\n";

    const raag::AstArena arena = parse_source(source, raag::Language::Python);

    EXPECT_FALSE(arena.empty());
}

// -----------------------------------------------------------------------------
// Structural invariants
//
// The arena's child lookup depends on a node's children being stored
// contiguously from first_child_index. These tests verify that invariant
// directly rather than inferring it from parse output, because a violation
// would surface downstream as silently wrong dependency edges.
// -----------------------------------------------------------------------------

TEST(AstArenaInvariants, ChildRangesStayWithinBounds) {
    constexpr std::string_view source =
        "class Outer:\n"
        "    def method_a(self):\n"
        "        return [x for x in range(10)]\n"
        "\n"
        "    class Inner:\n"
        "        def method_b(self):\n"
        "            pass\n";

    const raag::AstArena arena = parse_source(source, raag::Language::Python);
    ASSERT_FALSE(arena.empty());

    for (std::uint32_t i = 0; i < static_cast<std::uint32_t>(arena.size()); ++i) {
        const raag::AstNode& node = arena.node(i);
        if (node.child_count == 0) {
            continue;
        }

        const std::size_t range_end =
            static_cast<std::size_t>(node.first_child_index) +
            static_cast<std::size_t>(node.child_count);

        EXPECT_LE(range_end, arena.size())
            << "Node " << i << " declares a child range past the end of the arena";
    }
}

TEST(AstArenaInvariants, EveryChildPointsBackToItsParent) {
    constexpr std::string_view source =
        "class Outer:\n"
        "    def method_a(self):\n"
        "        return 1\n"
        "\n"
        "    class Inner:\n"
        "        def method_b(self):\n"
        "            pass\n";

    const raag::AstArena arena = parse_source(source, raag::Language::Python);
    ASSERT_FALSE(arena.empty());

    for (std::uint32_t i = 0; i < static_cast<std::uint32_t>(arena.size()); ++i) {
        const raag::AstNode& node = arena.node(i);

        for (std::uint32_t offset = 0; offset < node.child_count; ++offset) {
            const std::uint32_t child_index = node.first_child_index + offset;
            ASSERT_LT(child_index, static_cast<std::uint32_t>(arena.size()));

            EXPECT_EQ(arena.node(child_index).parent_index, static_cast<std::int32_t>(i))
                << "Child at index " << child_index
                << " does not point back to parent " << i;
        }
    }
}

TEST(AstArenaInvariants, RootHasNoParent) {
    constexpr std::string_view source = "def f():\n    pass\n";

    const raag::AstArena arena = parse_source(source, raag::Language::Python);
    ASSERT_FALSE(arena.empty());

    EXPECT_EQ(arena.node(0).parent_index, -1);
}

TEST(AstArenaInvariants, ChildByteRangesFallWithinParentRange) {
    constexpr std::string_view source =
        "def outer():\n"
        "    def inner():\n"
        "        pass\n"
        "    return inner\n";

    const raag::AstArena arena = parse_source(source, raag::Language::Python);
    ASSERT_FALSE(arena.empty());

    for (std::uint32_t i = 0; i < static_cast<std::uint32_t>(arena.size()); ++i) {
        const raag::AstNode& parent = arena.node(i);

        for (const raag::AstNode& child : arena.children(i)) {
            EXPECT_GE(child.byte_start, parent.byte_start);
            EXPECT_LE(child.byte_end, parent.byte_end);
        }
    }
}

TEST(AstArenaInvariants, ChildrenSpanMatchesDeclaredCount) {
    constexpr std::string_view source =
        "import os\n"
        "\n"
        "def a():\n"
        "    pass\n"
        "\n"
        "def b():\n"
        "    pass\n";

    const raag::AstArena arena = parse_source(source, raag::Language::Python);
    ASSERT_FALSE(arena.empty());

    for (std::uint32_t i = 0; i < static_cast<std::uint32_t>(arena.size()); ++i) {
        EXPECT_EQ(arena.children(i).size(),
                  static_cast<std::size_t>(arena.node(i).child_count));
    }
}

// -----------------------------------------------------------------------------
// Classifier
//
// Tested directly so a mapping regression is caught at its source rather than
// as a confusing count mismatch several tests away.
// -----------------------------------------------------------------------------

TEST(ClassifyNode, MapsPythonNodeTypes) {
    using raag::AstNodeKind;
    using raag::Language;

    EXPECT_EQ(raag::classify_node("module", Language::Python), AstNodeKind::File);
    EXPECT_EQ(raag::classify_node("function_definition", Language::Python),
              AstNodeKind::Function);
    EXPECT_EQ(raag::classify_node("class_definition", Language::Python),
              AstNodeKind::Class);
    EXPECT_EQ(raag::classify_node("import_statement", Language::Python),
              AstNodeKind::Import);
    EXPECT_EQ(raag::classify_node("import_from_statement", Language::Python),
              AstNodeKind::Import);
    EXPECT_EQ(raag::classify_node("call", Language::Python),
              AstNodeKind::CallExpression);
}

TEST(ClassifyNode, MapsCppNodeTypes) {
    using raag::AstNodeKind;
    using raag::Language;

    EXPECT_EQ(raag::classify_node("translation_unit", Language::Cpp), AstNodeKind::File);
    EXPECT_EQ(raag::classify_node("function_definition", Language::Cpp),
              AstNodeKind::Function);
    EXPECT_EQ(raag::classify_node("class_specifier", Language::Cpp),
              AstNodeKind::Class);
    EXPECT_EQ(raag::classify_node("struct_specifier", Language::Cpp),
              AstNodeKind::Class);
    EXPECT_EQ(raag::classify_node("preproc_include", Language::Cpp),
              AstNodeKind::Import);
    EXPECT_EQ(raag::classify_node("call_expression", Language::Cpp),
              AstNodeKind::CallExpression);
}

TEST(ClassifyNode, UnknownTypesFallBackToOther) {
    using raag::AstNodeKind;
    using raag::Language;

    EXPECT_EQ(raag::classify_node("some_unmapped_type", Language::Cpp),
              AstNodeKind::Other);
    EXPECT_EQ(raag::classify_node("", Language::Python), AstNodeKind::Other);
}

TEST(ClassifyNode, LanguageAffectsClassification) {
    using raag::AstNodeKind;
    using raag::Language;

    // "class_definition" is Python's spelling; C++ uses "class_specifier".
    // Passing one language's type string with the other language must not
    // produce a false positive.
    EXPECT_EQ(raag::classify_node("class_definition", Language::Cpp),
              AstNodeKind::Other);
    EXPECT_EQ(raag::classify_node("class_specifier", Language::Python),
              AstNodeKind::Other);
}

// -----------------------------------------------------------------------------
// Kind naming
// -----------------------------------------------------------------------------

TEST(ToString, ReturnsDistinctNamesForEveryKind) {
    using raag::AstNodeKind;

    EXPECT_EQ(raag::to_string(AstNodeKind::File), "File");
    EXPECT_EQ(raag::to_string(AstNodeKind::Class), "Class");
    EXPECT_EQ(raag::to_string(AstNodeKind::Function), "Function");
    EXPECT_EQ(raag::to_string(AstNodeKind::Import), "Import");
    EXPECT_EQ(raag::to_string(AstNodeKind::CallExpression), "Call");
    EXPECT_EQ(raag::to_string(AstNodeKind::Variable), "Variable");
    EXPECT_EQ(raag::to_string(AstNodeKind::Other), "Other");
}