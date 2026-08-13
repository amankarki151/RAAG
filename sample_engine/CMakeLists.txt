# =============================================================================
# RAAG — Sample Engine (Extraction Layer)
#
# C++20 static analysis engine. Traverses a repository, parses sources via
# Tree-sitter, and normalizes the result into an internal AST representation.
#
# Targets:
#   raag_sample_core  — static library holding all engine logic
#   raag_sample       — thin CLI executable linking against the core
#
# Options:
#   RAAG_BUILD_TESTS        (default ON)   Build the CTest/GoogleTest suite
#   RAAG_ENABLE_SANITIZERS  (default OFF)  ASan + UBSan, Debug builds only
# =============================================================================

cmake_minimum_required(VERSION 3.28)

# C is required in addition to CXX: Tree-sitter's core library and every
# grammar are plain C sources. Without this, CMake refuses to compile them.
project(raag_sample
    VERSION 0.1.0
    DESCRIPTION "RAAG Sample Engine — high-throughput source extraction layer"
    LANGUAGES CXX C
)

# -----------------------------------------------------------------------------
# Standards
# -----------------------------------------------------------------------------

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Disable compiler-specific extensions (-std=c++20 rather than -std=gnu++20)
# so the code stays portable across GCC, Clang, and AppleClang.
set(CMAKE_CXX_EXTENSIONS OFF)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

# Emit compile_commands.json for clangd and VS Code IntelliSense. This lands in
# the build directory; the .gitignore excludes it since it holds absolute paths.
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Default to Debug when no build type is specified. Without this, single-config
# generators (Makefiles, Ninja) build with no optimization AND no debug info.
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Debug CACHE STRING "Build type" FORCE)
    set_property(CACHE CMAKE_BUILD_TYPE PROPERTY STRINGS
        Debug Release RelWithDebInfo MinSizeRel)
endif()

# Position-independent code: required so this static library can later be
# linked into the Pybind11 shared module without relinking everything.
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# -----------------------------------------------------------------------------
# Options
# -----------------------------------------------------------------------------

option(RAAG_BUILD_TESTS "Build the RAAG Sample Engine test suite" ON)
option(RAAG_ENABLE_SANITIZERS "Enable AddressSanitizer and UBSan (Debug only)" OFF)

# -----------------------------------------------------------------------------
# Dependencies
#
# All third-party code is vendored via FetchContent rather than expected on the
# system. A fresh clone builds with no manual dependency installation.
#
# VERSION PINS: every dependency is pinned to a release tag, never a branch.
# A moving branch means two developers can build different code from the same
# commit, which makes bug reports irreproducible.
# -----------------------------------------------------------------------------

include(FetchContent)

# Silence the "DOWNLOAD_EXTRACT_TIMESTAMP" and dev warnings coming out of
# third-party projects — they are noise we cannot fix.
set(FETCHCONTENT_QUIET OFF)

# --- Tree-sitter core --------------------------------------------------------
# Recent releases ship a CMakeLists.txt, but its presence and target names have
# changed across versions. Building the amalgamated lib/src/lib.c ourselves is
# version-independent and is a single translation unit, so it costs nothing.
FetchContent_Declare(tree_sitter
    GIT_REPOSITORY https://github.com/tree-sitter/tree-sitter.git
    GIT_TAG        v0.24.4
    GIT_SHALLOW    TRUE
    # Point SOURCE_SUBDIR at a path that does not exist. This makes
    # FetchContent_MakeAvailable download the sources WITHOUT running
    # add_subdirectory() on them, so we control the target definition below.
    SOURCE_SUBDIR  cmake-does-not-exist
)

# --- Grammars ----------------------------------------------------------------
# Grammar repositories contain no build system at all — just generated
# src/parser.c (and sometimes src/scanner.c). Same SOURCE_SUBDIR trick applies.
FetchContent_Declare(tree_sitter_cpp
    GIT_REPOSITORY https://github.com/tree-sitter/tree-sitter-cpp.git
    GIT_TAG        v0.23.4
    GIT_SHALLOW    TRUE
    SOURCE_SUBDIR  cmake-does-not-exist
)

FetchContent_Declare(tree_sitter_python
    GIT_REPOSITORY https://github.com/tree-sitter/tree-sitter-python.git
    GIT_TAG        v0.23.6
    GIT_SHALLOW    TRUE
    SOURCE_SUBDIR  cmake-does-not-exist
)

FetchContent_MakeAvailable(tree_sitter tree_sitter_cpp tree_sitter_python)

# --- Build the Tree-sitter core runtime -------------------------------------
add_library(tree-sitter STATIC
    ${tree_sitter_SOURCE_DIR}/lib/src/lib.c
)

target_include_directories(tree-sitter
    PUBLIC
        ${tree_sitter_SOURCE_DIR}/lib/include
    PRIVATE
        ${tree_sitter_SOURCE_DIR}/lib/src
)

# SYSTEM include semantics for consumers: suppresses warnings originating in
# third-party headers, which we cannot fix and do not want failing our build.
set_target_properties(tree-sitter PROPERTIES
    C_STANDARD 11
    SYSTEM TRUE
)

# --- Grammar target helper ---------------------------------------------------
# Every grammar follows the same shape: a mandatory src/parser.c and an
# optional src/scanner.c for languages needing custom lexing (C++ and Python
# both do). This function removes the copy-paste.
function(raag_add_tree_sitter_grammar target_name source_dir)
    set(grammar_sources "${source_dir}/src/parser.c")

    # External scanners handle context-sensitive lexing that the generated
    # parser cannot express — Python's indentation, C++ raw string literals.
    if(EXISTS "${source_dir}/src/scanner.c")
        list(APPEND grammar_sources "${source_dir}/src/scanner.c")
    endif()

    add_library(${target_name} STATIC ${grammar_sources})

    # Grammars include "tree_sitter/parser.h", which each grammar repo vendors
    # under its own src/ directory.
    target_include_directories(${target_name}
        PUBLIC  "${source_dir}/src"
        PRIVATE "${source_dir}/src/tree_sitter"
    )

    target_link_libraries(${target_name} PUBLIC tree-sitter)

    set_target_properties(${target_name} PROPERTIES
        C_STANDARD 11
        SYSTEM TRUE
    )
endfunction()

raag_add_tree_sitter_grammar(tree-sitter-cpp    "${tree_sitter_cpp_SOURCE_DIR}")
raag_add_tree_sitter_grammar(tree-sitter-python "${tree_sitter_python_SOURCE_DIR}")

# -----------------------------------------------------------------------------
# Warning configuration
#
# Applied to RAAG's own targets only, via an INTERFACE library. Applying these
# globally through CMAKE_CXX_FLAGS would also hit Tree-sitter and GoogleTest,
# whose warnings we cannot fix — with -Werror that breaks the build outright.
# -----------------------------------------------------------------------------

add_library(raag_warnings INTERFACE)

if(MSVC)
    target_compile_options(raag_warnings INTERFACE /W4 /permissive-)
    # Warnings-as-errors in Debug only: developers get fast feedback locally,
    # while Release builds of a tagged version never fail on a new compiler's
    # newly-added warning.
    target_compile_options(raag_warnings INTERFACE $<$<CONFIG:Debug>:/WX>)
else()
    target_compile_options(raag_warnings INTERFACE
        -Wall
        -Wextra
        -Wpedantic
        -Wshadow              # Shadowed variables — a common source of bugs
        -Wnon-virtual-dtor    # Deleting through a base pointer without a
                              # virtual destructor is undefined behavior
        -Wcast-align
        -Wunused
        -Woverloaded-virtual
        -Wconversion          # Implicit narrowing, e.g. size_t -> uint32_t
        -Wsign-conversion
        -Wdouble-promotion
        -Wformat=2
    )
    target_compile_options(raag_warnings INTERFACE $<$<CONFIG:Debug>:-Werror>)
endif()

# -----------------------------------------------------------------------------
# Sanitizer configuration
#
# ASan and UBSan catch memory errors and undefined behavior at runtime. They
# must be passed to BOTH the compiler and the linker — a common mistake is
# setting only compile options, which produces confusing link errors.
#
# Opt-in rather than default-on because sanitized binaries run substantially
# slower, which distorts the parsing benchmarks this engine is measured by.
# -----------------------------------------------------------------------------

add_library(raag_sanitizers INTERFACE)

if(RAAG_ENABLE_SANITIZERS)
    if(MSVC)
        message(WARNING
            "RAAG_ENABLE_SANITIZERS: only AddressSanitizer is available on MSVC; "
            "UndefinedBehaviorSanitizer will be skipped.")
        target_compile_options(raag_sanitizers INTERFACE $<$<CONFIG:Debug>:/fsanitize=address>)
    else()
        set(RAAG_SANITIZER_FLAGS
            -fsanitize=address
            -fsanitize=undefined
            -fno-omit-frame-pointer   # Readable stack traces in sanitizer output
            -fno-sanitize-recover=all # Abort on first error rather than continuing
        )
        target_compile_options(raag_sanitizers INTERFACE
            $<$<CONFIG:Debug>:${RAAG_SANITIZER_FLAGS}>)
        target_link_options(raag_sanitizers INTERFACE
            $<$<CONFIG:Debug>:${RAAG_SANITIZER_FLAGS}>)
    endif()

    if(NOT CMAKE_BUILD_TYPE STREQUAL "Debug")
        message(STATUS
            "RAAG_ENABLE_SANITIZERS is ON but the build type is "
            "'${CMAKE_BUILD_TYPE}'. Sanitizers are gated to Debug and will "
            "not be active in this configuration.")
    endif()
endif()

# -----------------------------------------------------------------------------
# Core library
#
# All engine logic lives here rather than in the executable so that both the
# CLI and the test suite link against identical code. Testing a static library
# is possible; testing a main() is not.
#
# Sources are listed explicitly rather than globbed. file(GLOB) does not
# re-run when files are added, producing "why isn't my new file compiling"
# confusion. Explicit lists fail loudly instead.
# -----------------------------------------------------------------------------

add_library(raag_sample_core STATIC
    src/ast_node.cpp
    src/tree_sitter_parser.cpp
    src/ast_builder.cpp
    src/thread_pool.cpp
    src/file_walker.cpp
    src/snapshot.cpp
)


target_include_directories(raag_sample_core
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>
)

target_link_libraries(raag_sample_core
    PUBLIC
        tree-sitter
        tree-sitter-cpp
        tree-sitter-python
    PRIVATE
        raag_warnings
        raag_sanitizers
)

# Threads: the std::jthread worker pool arrives on Day 2, but declaring the
# dependency now avoids a confusing link failure later on Linux, where
# std::thread requires explicit pthread linkage.
find_package(Threads REQUIRED)
target_link_libraries(raag_sample_core PUBLIC Threads::Threads)

set_target_properties(raag_sample_core PROPERTIES
    OUTPUT_NAME "raag_sample_core"
    CXX_VISIBILITY_PRESET hidden      # Keeps symbols internal by default,
    VISIBILITY_INLINES_HIDDEN ON      # relevant once Pybind11 wraps this
)

# -----------------------------------------------------------------------------
# Executable
#
# Deliberately thin: argument handling and output formatting only. Every
# decision worth testing lives in raag_sample_core.
# -----------------------------------------------------------------------------

add_executable(raag_sample src/main.cpp)

target_link_libraries(raag_sample
    PRIVATE
        raag_sample_core
        raag_warnings
        raag_sanitizers
)

# Place the binary at build/raag_sample regardless of generator layout, so the
# path referenced in documentation and .gitignore stays accurate.
set_target_properties(raag_sample PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}"
)

# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------

if(RAAG_BUILD_TESTS)
    # enable_testing() must be called in the top-level list file, not inside
    # tests/CMakeLists.txt, or `ctest` finds no tests from the build root.
    enable_testing()

    FetchContent_Declare(googletest
        GIT_REPOSITORY https://github.com/google/googletest.git
        GIT_TAG        v1.15.2
        GIT_SHALLOW    TRUE
    )

    # Prevents GoogleTest from overriding this project's compiler and linker
    # settings on Windows. Harmless elsewhere; required there.
    set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)

    # Do not install GoogleTest alongside RAAG when `cmake --install` runs.
    set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)

    FetchContent_MakeAvailable(googletest)

    add_subdirectory(tests)
endif()

# -----------------------------------------------------------------------------
# Configuration summary
#
# Printed at configure time so a build's settings are visible in CI logs and
# bug reports without needing to inspect CMakeCache.txt.
# -----------------------------------------------------------------------------

message(STATUS "")
message(STATUS "RAAG Sample Engine ${PROJECT_VERSION}")
message(STATUS "  Build type .............. ${CMAKE_BUILD_TYPE}")
message(STATUS "  C++ compiler ............ ${CMAKE_CXX_COMPILER_ID} ${CMAKE_CXX_COMPILER_VERSION}")
message(STATUS "  C++ standard ............ ${CMAKE_CXX_STANDARD}")
message(STATUS "  Build tests ............. ${RAAG_BUILD_TESTS}")
message(STATUS "  Sanitizers .............. ${RAAG_ENABLE_SANITIZERS}")
message(STATUS "")