// =============================================================================
// RAAG — Sample Engine CLI
//
// Day 1 scope: parse a single file and print its AST.
//
// Deliberately thin. Argument handling, file I/O, and output formatting live
// here; everything worth testing lives in raag_sample_core, because a static
// library can be linked into a test binary and a main() cannot.
// =============================================================================

#include <cstdlib>
#include <exception>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

#include "raag/ast_builder.hpp"
#include "raag/ast_node.hpp"
#include "raag/tree_sitter_parser.hpp"

namespace {

/// Selects a grammar from a file extension.
///
/// Returns nullopt for anything unrecognized rather than guessing. Parsing a
/// file with the wrong grammar produces a tree full of ERROR nodes and
/// nonsense structure, which is harder to diagnose than an upfront refusal.
std::optional<raag::Language> language_from_path(const std::filesystem::path& path) {
    const std::string extension = path.extension().string();

    if (extension == ".cpp" || extension == ".cc" || extension == ".cxx" ||
        extension == ".hpp" || extension == ".hh" || extension == ".hxx" ||
        extension == ".h") {
        return raag::Language::Cpp;
    }
    if (extension == ".py" || extension == ".pyi") {
        return raag::Language::Python;
    }

    return std::nullopt;
}

/// Reads a file whole.
///
/// Opened in binary mode so byte offsets from Tree-sitter line up with the
/// buffer exactly. Text mode on some platforms translates line endings, which
/// would shift every offset after the first newline.
std::optional<std::string> read_file(const std::filesystem::path& path,
                                     std::string& error_message) {
    std::error_code ec;

    if (!std::filesystem::exists(path, ec)) {
        error_message = "file does not exist";
        return std::nullopt;
    }
    if (std::filesystem::is_directory(path, ec)) {
        error_message = "path is a directory, not a file";
        return std::nullopt;
    }

    std::ifstream stream(path, std::ios::in | std::ios::binary);
    if (!stream) {
        // The most common cause is a permissions failure. The standard library
        // gives no portable way to distinguish it here, so the message stays
        // honest about the uncertainty.
        error_message = "could not open file (check read permissions)";
        return std::nullopt;
    }

    std::ostringstream buffer;
    buffer << stream.rdbuf();

    if (stream.bad()) {
        error_message = "I/O error while reading file";
        return std::nullopt;
    }

    return buffer.str();
}

/// Prints the arena as an indented tree.
///
/// Iterative, using an explicit stack, for the same reason the builder is:
/// a deeply nested file should not be able to exhaust the call stack.
void print_ast(const raag::AstArena& arena) {
    if (arena.empty()) {
        std::cout << "(empty AST)\n";
        return;
    }

    struct Frame {
        std::uint32_t index;
        int depth;
    };

    std::vector<Frame> stack;
    stack.push_back({0, 0});

    while (!stack.empty()) {
        const Frame frame = stack.back();
        stack.pop_back();

        const raag::AstNode& node = arena.node(frame.index);

        std::cout << std::string(static_cast<std::size_t>(frame.depth) * 2, ' ')
                  << raag::to_string(node.kind);

        if (!node.name.empty()) {
            std::cout << " '" << node.name << "'";
        }

        std::cout << "  [" << node.byte_start << ".." << node.byte_end << "]\n";

        // Pushed in reverse so siblings pop in source order.
        for (std::uint32_t i = node.child_count; i > 0; --i) {
            stack.push_back({node.first_child_index + (i - 1), frame.depth + 1});
        }
    }
}

void print_usage(std::string_view program_name) {
    std::cerr << "RAAG Sample Engine — source extraction layer\n\n"
              << "Usage: " << program_name << " <source-file>\n\n"
              << "Supported: .cpp .cc .cxx .hpp .hh .hxx .h .py .pyi\n";
}

}  // namespace

int main(int argc, char* argv[]) {
    const std::string_view program_name = (argc > 0) ? argv[0] : "raag_sample";

    if (argc != 2) {
        print_usage(program_name);
        return EXIT_FAILURE;
    }

    const std::filesystem::path source_path{argv[1]};

    const std::optional<raag::Language> language = language_from_path(source_path);
    if (!language.has_value()) {
        std::cerr << "error: unsupported file extension '"
                  << source_path.extension().string() << "'\n";
        print_usage(program_name);
        return EXIT_FAILURE;
    }

    std::string read_error;
    const std::optional<std::string> source = read_file(source_path, read_error);
    if (!source.has_value()) {
        std::cerr << "error: " << source_path.string() << ": " << read_error << "\n";
        return EXIT_FAILURE;
    }

    // Everything below can throw — parser construction on an ABI mismatch,
    // allocation under memory pressure. Caught here so the CLI reports a
    // message rather than terminating via an unhandled exception.
    try {
        raag::TreeSitterParser parser(*language);

        if (!parser.parse(*source)) {
            std::cerr << "error: failed to parse " << source_path.string() << "\n";
            return EXIT_FAILURE;
        }

        const raag::AstArena arena =
            raag::build_ast(parser.root_node(), *source, *language);

        std::cout << "File: " << source_path.string() << "\n"
                  << "Nodes: " << arena.size() << "\n"
                  << "----------------------------------------\n";

        print_ast(arena);

        return EXIT_SUCCESS;

    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << "\n";
        return EXIT_FAILURE;
    }
}
