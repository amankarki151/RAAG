#pragma once

// =============================================================================
// RAAG — Binary snapshot format
//
// The contract between the Sample Engine (C++) and the Tune Engine (Python).
// One snapshot holds the parsed ASTs of every file in a repository.
//
// LAYOUT
//
//   Header
//     magic          4 bytes   "RAAG"
//     schema_version u32
//     file_count     u32
//
//   Per file, repeated file_count times
//     path_length    u32
//     path           path_length bytes, UTF-8, no terminator
//     node_count     u32
//
//     Per node, repeated node_count times
//       kind               u8
//       name_length        u32
//       name               name_length bytes
//       byte_start         u32
//       byte_end           u32
//       first_child_index  u32
//       child_count        u32
//       parent_index       i32   (two's complement, -1 for the root)
//
// Every integer is written little-endian, one byte at a time. The obvious
// alternative — memcpy of the AstNode struct — is wrong twice over: AstNode
// contains a std::string, so it is not trivially copyable and its bytes are a
// pointer rather than the text; and even for a POD the result would depend on
// the compiler's padding choices and the host's endianness, so a file written
// on one machine could not be read on another.
//
// SCHEMA VERSIONING
//
// kSchemaVersion is independent of the platform version. Any change to the
// layout above is a breaking change to the Sample-to-Tune contract and must
// increment it, even when the release is only a minor version bump. A reader
// encountering an unknown version fails immediately rather than misinterpreting
// bytes, which would surface much later as inexplicable graph errors.
// =============================================================================

#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "raag/ast_node.hpp"

namespace raag {

/// Current on-disk schema version. Increment on any layout change.
inline constexpr std::uint32_t kSchemaVersion = 1;

/// Four-byte file signature, checked before anything else is read.
inline constexpr char kSnapshotMagic[4] = {'R', 'A', 'A', 'G'};

/// One file's parsed contents as read back from a snapshot.
struct SnapshotEntry {
    std::string path;
    AstArena arena;
};

/// Writes `entries` to `output_path`.
///
/// Creates parent directories if they do not exist. Returns false on any I/O
/// failure; the caller decides whether that is fatal.
[[nodiscard]] bool write_snapshot(
    const std::filesystem::path& output_path,
    const std::vector<std::pair<std::string, AstArena>>& entries);

/// Reads a snapshot written by write_snapshot.
///
/// Returns nullopt when the file is missing, the magic bytes do not match, the
/// schema version is unrecognized, or the stream ends mid-record. A truncated
/// file is rejected rather than partially accepted, because a half-read arena
/// would produce child index ranges pointing past the end of the node list.
[[nodiscard]] std::optional<std::vector<SnapshotEntry>> read_snapshot(
    const std::filesystem::path& input_path);

}  // namespace raag