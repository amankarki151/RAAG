#include "raag/snapshot.hpp"

#include <bit>
#include <cstring>
#include <fstream>
#include <ios>
#include <limits>
#include <system_error>

namespace raag {
namespace {

/// Highest AstNodeKind value, used to reject a corrupt kind byte on read.
constexpr std::uint8_t kMaxKindValue = static_cast<std::uint8_t>(AstNodeKind::Other);

/// Guards against a corrupt length field causing an enormous allocation.
/// No realistic identifier or path approaches this.
constexpr std::uint32_t kMaxStringLength = 1u << 20;

// --- Writing -----------------------------------------------------------------

void write_u8(std::ostream& out, std::uint8_t value) {
    const char byte = static_cast<char>(value);
    out.write(&byte, 1);
}

void write_u32(std::ostream& out, std::uint32_t value) {
    // Byte order is fixed by the format, not inherited from the host. Shifting
    // and masking produces the same bytes on a big-endian machine as on a
    // little-endian one.
    const char bytes[4] = {
        static_cast<char>(value & 0xFFu),
        static_cast<char>((value >> 8) & 0xFFu),
        static_cast<char>((value >> 16) & 0xFFu),
        static_cast<char>((value >> 24) & 0xFFu),
    };
    out.write(bytes, 4);
}

void write_i32(std::ostream& out, std::int32_t value) {
    // std::bit_cast preserves the two's-complement bit pattern without the
    // implementation-defined behavior of a signed-to-unsigned conversion.
    write_u32(out, std::bit_cast<std::uint32_t>(value));
}

void write_string(std::ostream& out, const std::string& value) {
    write_u32(out, static_cast<std::uint32_t>(value.size()));
    if (!value.empty()) {
        out.write(value.data(), static_cast<std::streamsize>(value.size()));
    }
}

// --- Reading -----------------------------------------------------------------

bool read_u8(std::istream& in, std::uint8_t& value) {
    char byte = 0;
    if (!in.read(&byte, 1)) {
        return false;
    }
    value = static_cast<std::uint8_t>(static_cast<unsigned char>(byte));
    return true;
}

bool read_u32(std::istream& in, std::uint32_t& value) {
    char bytes[4] = {};
    if (!in.read(bytes, 4)) {
        return false;
    }

    value = static_cast<std::uint32_t>(static_cast<unsigned char>(bytes[0]));
    value |= static_cast<std::uint32_t>(static_cast<unsigned char>(bytes[1])) << 8;
    value |= static_cast<std::uint32_t>(static_cast<unsigned char>(bytes[2])) << 16;
    value |= static_cast<std::uint32_t>(static_cast<unsigned char>(bytes[3])) << 24;
    return true;
}

bool read_i32(std::istream& in, std::int32_t& value) {
    std::uint32_t raw = 0;
    if (!read_u32(in, raw)) {
        return false;
    }
    value = std::bit_cast<std::int32_t>(raw);
    return true;
}

bool read_string(std::istream& in, std::string& value) {
    std::uint32_t length = 0;
    if (!read_u32(in, length)) {
        return false;
    }

    // Checked before resizing. Without this, a corrupt four-byte length could
    // request a multi-gigabyte allocation from a file of a few hundred bytes.
    if (length > kMaxStringLength) {
        return false;
    }

    value.resize(static_cast<std::size_t>(length));
    if (length == 0) {
        return true;
    }

    return static_cast<bool>(in.read(value.data(), static_cast<std::streamsize>(length)));
}

}  // namespace

bool write_snapshot(const std::filesystem::path& output_path,
                    const std::vector<std::pair<std::string, AstArena>>& entries) {
    std::error_code ec;
    const std::filesystem::path parent = output_path.parent_path();
    if (!parent.empty()) {
        std::filesystem::create_directories(parent, ec);
        if (ec) {
            return false;
        }
    }

    // Binary mode matters: text mode would translate byte sequences that happen
    // to look like line endings on some platforms, corrupting the file.
    std::ofstream out(output_path, std::ios::out | std::ios::binary | std::ios::trunc);
    if (!out) {
        return false;
    }

    out.write(kSnapshotMagic, 4);
    write_u32(out, kSchemaVersion);
    write_u32(out, static_cast<std::uint32_t>(entries.size()));

    for (const auto& [path, arena] : entries) {
        write_string(out, path);

        const std::span<const AstNode> nodes = arena.nodes();
        write_u32(out, static_cast<std::uint32_t>(nodes.size()));

        for (const AstNode& node : nodes) {
            write_u8(out, static_cast<std::uint8_t>(node.kind));
            write_string(out, node.name);
            write_u32(out, node.byte_start);
            write_u32(out, node.byte_end);
            write_u32(out, node.first_child_index);
            write_u32(out, node.child_count);
            write_i32(out, node.parent_index);
        }
    }

    out.flush();
    return static_cast<bool>(out);
}

std::optional<std::vector<SnapshotEntry>> read_snapshot(
    const std::filesystem::path& input_path) {
    std::ifstream in(input_path, std::ios::in | std::ios::binary);
    if (!in) {
        return std::nullopt;
    }

    char magic[4] = {};
    if (!in.read(magic, 4)) {
        return std::nullopt;
    }
    if (std::memcmp(magic, kSnapshotMagic, 4) != 0) {
        return std::nullopt;
    }

    std::uint32_t version = 0;
    if (!read_u32(in, version) || version != kSchemaVersion) {
        // Refusing an unknown version is the entire point of writing one. The
        // alternative is reading a future layout as if it were this one, which
        // fails silently rather than loudly.
        return std::nullopt;
    }

    std::uint32_t file_count = 0;
    if (!read_u32(in, file_count)) {
        return std::nullopt;
    }

    std::vector<SnapshotEntry> entries;
    entries.reserve(static_cast<std::size_t>(file_count));

    for (std::uint32_t file_index = 0; file_index < file_count; ++file_index) {
        SnapshotEntry entry;

        if (!read_string(in, entry.path)) {
            return std::nullopt;
        }

        std::uint32_t node_count = 0;
        if (!read_u32(in, node_count)) {
            return std::nullopt;
        }

        entry.arena.reserve(static_cast<std::size_t>(node_count));

        for (std::uint32_t node_index = 0; node_index < node_count; ++node_index) {
            AstNode node;

            std::uint8_t kind_value = 0;
            if (!read_u8(in, kind_value) || kind_value > kMaxKindValue) {
                return std::nullopt;
            }
            node.kind = static_cast<AstNodeKind>(kind_value);

            if (!read_string(in, node.name) ||
                !read_u32(in, node.byte_start) ||
                !read_u32(in, node.byte_end) ||
                !read_u32(in, node.first_child_index) ||
                !read_u32(in, node.child_count) ||
                !read_i32(in, node.parent_index)) {
                return std::nullopt;
            }

            static_cast<void>(entry.arena.add_node(std::move(node)));
        }

        entries.push_back(std::move(entry));
    }

    return entries;
}

}  // namespace raag