#include "raag/file_walker.hpp"

#include <algorithm>
#include <array>
#include <string>
#include <string_view>
#include <system_error>

namespace raag {
namespace {

constexpr std::array<std::string_view, 7> kCppExtensions{
    ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".h"};

constexpr std::array<std::string_view, 2> kPythonExtensions{".py", ".pyi"};

/// Directories whose contents are generated, vendored, or metadata rather than
/// source the repository authors wrote.
constexpr std::array<std::string_view, 10> kExcludedDirectories{
    ".git", "build", "build-release", "_deps", "node_modules",
    "venv", ".venv", "__pycache__", "cmake-build-debug", "cmake-build-release"};

}  // namespace

std::optional<Language> language_for_extension(const std::filesystem::path& path) {
    const std::string extension = path.extension().string();
    if (extension.empty()) {
        return std::nullopt;
    }

    if (std::find(kCppExtensions.begin(), kCppExtensions.end(), extension) !=
        kCppExtensions.end()) {
        return Language::Cpp;
    }

    if (std::find(kPythonExtensions.begin(), kPythonExtensions.end(), extension) !=
        kPythonExtensions.end()) {
        return Language::Python;
    }

    return std::nullopt;
}

bool is_excluded_directory(const std::filesystem::path& directory_name) {
    const std::string name = directory_name.string();

    // Hidden directories are skipped wholesale. Tool caches and editor state
    // live under dotted names, and none of it is repository source.
    if (name.size() > 1 && name.front() == '.' && name != "..") {
        return true;
    }

    return std::find(kExcludedDirectories.begin(), kExcludedDirectories.end(), name) !=
           kExcludedDirectories.end();
}

std::vector<std::filesystem::path> collect_source_files(
    const std::filesystem::path& root) {
    std::vector<std::filesystem::path> files;

    std::error_code ec;
    if (!std::filesystem::is_directory(root, ec) || ec) {
        return files;
    }

    // The error_code overloads are used throughout rather than the throwing
    // ones. A single unreadable directory in a large repository — a permission
    // issue, a broken symlink, a file removed mid-walk — should skip that entry
    // and continue, not abort a traversal that has already covered thousands of
    // files.
    std::filesystem::recursive_directory_iterator iterator(
        root,
        std::filesystem::directory_options::skip_permission_denied,
        ec);

    if (ec) {
        return files;
    }

    const std::filesystem::recursive_directory_iterator end;

    while (iterator != end) {
        const std::filesystem::directory_entry& entry = *iterator;

        std::error_code entry_ec;
        if (entry.is_directory(entry_ec) && !entry_ec) {
            if (is_excluded_directory(entry.path().filename())) {
                // Prunes the whole subtree rather than descending into it and
                // filtering each file individually.
                iterator.disable_recursion_pending();
            }
        } else if (entry.is_regular_file(entry_ec) && !entry_ec) {
            if (language_for_extension(entry.path()).has_value()) {
                files.push_back(entry.path());
            }
        }

        iterator.increment(ec);
        if (ec) {
            break;
        }
    }

    return files;
}

}  // namespace raag