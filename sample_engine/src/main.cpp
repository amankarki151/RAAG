// =============================================================================
// RAAG — Sample Engine CLI
//
// Parses a single file or an entire repository, optionally writing a binary
// snapshot for the Tune Engine and optionally benchmarking the parallel path
// against a single-threaded baseline.
//
// Kept thin on purpose: argument handling, I/O, and formatting only. Anything
// worth testing lives in raag_sample_core, because a static library can be
// linked into a test binary and a main() cannot.
// =============================================================================

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <utility>
#include <vector>

#include "raag/ast_builder.hpp"
#include "raag/ast_node.hpp"
#include "raag/file_walker.hpp"
#include "raag/snapshot.hpp"
#include "raag/thread_pool.hpp"
#include "raag/tree_sitter_parser.hpp"

namespace {

struct Options {
    std::filesystem::path target;
    std::filesystem::path output = "snapshots/repo.raag.bin";
    std::size_t thread_count = 0;  // 0 selects hardware concurrency
    bool benchmark = false;
    bool quiet = false;
    bool write_snapshot_requested = false;
};

struct ParseResult {
    std::string path;
    raag::AstArena arena;
};

struct RunStatistics {
    std::size_t files_parsed = 0;
    std::size_t files_failed = 0;
    std::size_t total_nodes = 0;
    double elapsed_seconds = 0.0;
};

void print_usage(std::string_view program_name) {
    std::cerr
        << "RAAG Sample Engine - source extraction layer\n\n"
        << "Usage: " << program_name << " <path> [options]\n\n"
        << "  <path>              A source file, or a directory to parse recursively.\n\n"
        << "Options:\n"
        << "  --output <file>     Write a binary snapshot (default snapshots/repo.raag.bin)\n"
        << "  --threads <n>       Worker thread count (default: hardware concurrency)\n"
        << "  --benchmark         Time a single-threaded pass against the parallel one\n"
        << "  --quiet             Suppress per-node output\n"
        << "  --help              Show this message\n\n"
        << "Supported: .cpp .cc .cxx .hpp .hh .hxx .h .py .pyi\n";
}

/// Reads a whole file.
///
/// Binary mode so byte offsets from Tree-sitter index into the buffer exactly.
/// Text mode translates line endings on some platforms, which would shift every
/// offset after the first newline.
std::optional<std::string> read_file(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::in | std::ios::binary);
    if (!stream) {
        return std::nullopt;
    }

    std::ostringstream buffer;
    buffer << stream.rdbuf();

    if (stream.bad()) {
        return std::nullopt;
    }

    return buffer.str();
}

/// Prints an arena as an indented tree.
///
/// Iterative for the same reason the builder is: a deeply nested file should
/// not be able to exhaust the call stack.
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

/// Parses one file into an arena. Returns nullopt on any failure.
std::optional<raag::AstArena> parse_one(const std::filesystem::path& path,
                                        raag::TreeSitterParser& parser,
                                        raag::Language language) {
    const std::optional<std::string> source = read_file(path);
    if (!source.has_value()) {
        return std::nullopt;
    }

    if (!parser.parse(*source)) {
        return std::nullopt;
    }

    return raag::build_ast(parser.root_node(), *source, language);
}

/// Single-threaded baseline. Exists to give the parallel run something honest
/// to be measured against.
RunStatistics parse_sequential(const std::vector<std::filesystem::path>& files,
                               std::vector<ParseResult>* results) {
    RunStatistics stats;
    const auto start = std::chrono::steady_clock::now();

    // Two parsers reused across all files of each language. Constructing one
    // per file would measure allocator throughput rather than parsing.
    raag::TreeSitterParser cpp_parser(raag::Language::Cpp);
    raag::TreeSitterParser python_parser(raag::Language::Python);

    for (const std::filesystem::path& file : files) {
        const std::optional<raag::Language> language =
            raag::language_for_extension(file);
        if (!language.has_value()) {
            ++stats.files_failed;
            continue;
        }

        raag::TreeSitterParser& parser =
            (*language == raag::Language::Cpp) ? cpp_parser : python_parser;

        std::optional<raag::AstArena> arena = parse_one(file, parser, *language);
        if (!arena.has_value()) {
            ++stats.files_failed;
            continue;
        }

        ++stats.files_parsed;
        stats.total_nodes += arena->size();

        if (results != nullptr) {
            results->push_back({file.string(), std::move(*arena)});
        }
    }

    const auto end = std::chrono::steady_clock::now();
    stats.elapsed_seconds = std::chrono::duration<double>(end - start).count();
    return stats;
}

/// Parallel path. One task per file, distributed across the worker pool.
RunStatistics parse_parallel(const std::vector<std::filesystem::path>& files,
                             std::size_t thread_count,
                             std::vector<ParseResult>* results) {
    RunStatistics stats;
    std::mutex results_mutex;

    const auto start = std::chrono::steady_clock::now();

    {
        raag::ThreadPool pool(thread_count);

        for (const std::filesystem::path& file : files) {
            pool.submit([&file, &results_mutex, &stats, results] {
                const std::optional<raag::Language> language =
                    raag::language_for_extension(file);
                if (!language.has_value()) {
                    const std::lock_guard<std::mutex> lock(results_mutex);
                    ++stats.files_failed;
                    return;
                }

                // A TSParser holds mutable internal state and is not
                // thread-safe. Each task constructs its own rather than
                // sharing one, which would corrupt trees under concurrency in
                // ways that surface as random, unreproducible parse failures.
                raag::TreeSitterParser parser(*language);

                std::optional<raag::AstArena> arena =
                    parse_one(file, parser, *language);

                const std::lock_guard<std::mutex> lock(results_mutex);
                if (!arena.has_value()) {
                    ++stats.files_failed;
                    return;
                }

                ++stats.files_parsed;
                stats.total_nodes += arena->size();

                if (results != nullptr) {
                    results->push_back({file.string(), std::move(*arena)});
                }
            });
        }

        pool.wait_for_completion();
    }  // Pool destroyed here: workers stop and join before timing ends.

    const auto end = std::chrono::steady_clock::now();
    stats.elapsed_seconds = std::chrono::duration<double>(end - start).count();
    return stats;
}

void print_statistics(std::string_view label, const RunStatistics& stats) {
    const double files_per_second =
        (stats.elapsed_seconds > 0.0)
            ? static_cast<double>(stats.files_parsed) / stats.elapsed_seconds
            : 0.0;

    std::cout << std::left << std::setw(18) << label << std::right << std::fixed
              << std::setprecision(3) << std::setw(9) << stats.elapsed_seconds << " s"
              << std::setw(12) << std::setprecision(1) << files_per_second
              << " files/s\n";
}

std::optional<Options> parse_arguments(int argc, char* argv[]) {
    Options options;
    bool target_set = false;

    for (int i = 1; i < argc; ++i) {
        const std::string_view argument = argv[i];

        if (argument == "--help" || argument == "-h") {
            return std::nullopt;
        }

        if (argument == "--benchmark") {
            options.benchmark = true;
        } else if (argument == "--quiet") {
            options.quiet = true;
        } else if (argument == "--output") {
            if (i + 1 >= argc) {
                std::cerr << "error: --output requires a file path\n";
                return std::nullopt;
            }
            options.output = argv[++i];
            options.write_snapshot_requested = true;
        } else if (argument == "--threads") {
            if (i + 1 >= argc) {
                std::cerr << "error: --threads requires a count\n";
                return std::nullopt;
            }
            const int requested = std::atoi(argv[++i]);
            if (requested <= 0) {
                std::cerr << "error: --threads must be a positive integer\n";
                return std::nullopt;
            }
            options.thread_count = static_cast<std::size_t>(requested);
        } else if (argument.starts_with("--")) {
            std::cerr << "error: unknown option '" << argument << "'\n";
            return std::nullopt;
        } else if (!target_set) {
            options.target = argument;
            target_set = true;
        } else {
            std::cerr << "error: unexpected argument '" << argument << "'\n";
            return std::nullopt;
        }
    }

    if (!target_set) {
        return std::nullopt;
    }

    return options;
}

int run_single_file(const Options& options) {
    const std::optional<raag::Language> language =
        raag::language_for_extension(options.target);
    if (!language.has_value()) {
        std::cerr << "error: unsupported file extension '"
                  << options.target.extension().string() << "'\n";
        return EXIT_FAILURE;
    }

    raag::TreeSitterParser parser(*language);
    std::optional<raag::AstArena> arena = parse_one(options.target, parser, *language);

    if (!arena.has_value()) {
        std::cerr << "error: failed to read or parse " << options.target.string()
                  << "\n";
        return EXIT_FAILURE;
    }

    std::cout << "File: " << options.target.string() << "\n"
              << "Nodes: " << arena->size() << "\n"
              << "----------------------------------------\n";

    if (!options.quiet) {
        print_ast(*arena);
    }

    return EXIT_SUCCESS;
}

int run_directory(const Options& options) {
    const std::vector<std::filesystem::path> files =
        raag::collect_source_files(options.target);

    if (files.empty()) {
        std::cerr << "error: no parseable source files found under "
                  << options.target.string() << "\n";
        return EXIT_FAILURE;
    }

    std::cout << "Repository: " << options.target.string() << "\n"
              << "Files found: " << files.size() << "\n\n";

    if (options.benchmark) {
        std::cout << "Benchmark\n"
                  << "----------------------------------------------\n";

        const RunStatistics sequential = parse_sequential(files, nullptr);
        print_statistics("Single-threaded", sequential);

        const RunStatistics parallel =
            parse_parallel(files, options.thread_count, nullptr);
        print_statistics("Parallel", parallel);

        std::cout << "----------------------------------------------\n";

        if (parallel.elapsed_seconds > 0.0) {
            const double speedup =
                sequential.elapsed_seconds / parallel.elapsed_seconds;

            const unsigned int threads_used =
                (options.thread_count == 0)
                    ? std::thread::hardware_concurrency()
                    : static_cast<unsigned int>(options.thread_count);

            std::cout << "Speedup: " << std::fixed << std::setprecision(2) << speedup
                      << "x on " << threads_used << " threads\n";
        }

        std::cout << "Files parsed: " << parallel.files_parsed
                  << "   failed: " << parallel.files_failed
                  << "   nodes: " << parallel.total_nodes << "\n";

        return EXIT_SUCCESS;
    }

    std::vector<ParseResult> results;
    results.reserve(files.size());

    const RunStatistics stats = parse_parallel(files, options.thread_count, &results);

    std::cout << "Parsed " << stats.files_parsed << " files (" << stats.files_failed
              << " failed) in " << std::fixed << std::setprecision(3)
              << stats.elapsed_seconds << " s\n"
              << "Total nodes: " << stats.total_nodes << "\n";

    std::vector<std::pair<std::string, raag::AstArena>> entries;
    entries.reserve(results.size());
    for (ParseResult& result : results) {
        entries.emplace_back(std::move(result.path), std::move(result.arena));
    }

    if (!raag::write_snapshot(options.output, entries)) {
        std::cerr << "error: failed to write snapshot to " << options.output.string()
                  << "\n";
        return EXIT_FAILURE;
    }

    std::error_code ec;
    const std::uintmax_t size = std::filesystem::file_size(options.output, ec);
    std::cout << "Snapshot: " << options.output.string();
    if (!ec) {
        std::cout << " (" << size << " bytes)";
    }
    std::cout << "\n";

    return EXIT_SUCCESS;
}

}  // namespace

int main(int argc, char* argv[]) {
    const std::string_view program_name = (argc > 0) ? argv[0] : "raag_sample";

    const std::optional<Options> options = parse_arguments(argc, argv);
    if (!options.has_value()) {
        print_usage(program_name);
        return EXIT_FAILURE;
    }

    std::error_code ec;
    if (!std::filesystem::exists(options->target, ec) || ec) {
        std::cerr << "error: " << options->target.string() << " does not exist\n";
        return EXIT_FAILURE;
    }

    // Parser construction can throw on an ABI mismatch between the linked
    // Tree-sitter and a grammar. Caught here so the CLI reports a message
    // rather than terminating on an unhandled exception.
    try {
        const bool is_directory = std::filesystem::is_directory(options->target, ec);
        if (is_directory && !ec) {
            return run_directory(*options);
        }
        return run_single_file(*options);

    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << "\n";
        return EXIT_FAILURE;
    }
}