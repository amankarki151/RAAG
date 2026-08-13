// =============================================================================
// RAAG — Snapshot serialization tests
//
// The snapshot is the contract between the C++ extraction layer and the Python
// analytics layer. A silent corruption here becomes an inexplicable graph error
// several stages downstream, so the round-trip is asserted field by field
// rather than by node count alone.
// =============================================================================

#include <gtest/gtest.h>

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <string>
#include <utility>
#include <vector>

#include "raag/ast_node.hpp"
#include "raag/snapshot.hpp"

namespace {

/// Creates a unique temporary path for one test, cleaned up by TempFile.
class TempFile {
public:
    explicit TempFile(const std::string& name)
        : path_(std::filesystem::temp_directory_path() / ("raag_test_" + name)) {
        std::error_code ec;
        std::filesystem::remove(path_, ec);
    }

    TempFile(const TempFile&) = delete;
    TempFile& operator=(const TempFile&) = delete;
    TempFile(TempFile&&) = delete;
    TempFile& operator=(TempFile&&) = delete;

    ~TempFile() {
        std::error_code ec;
        std::filesystem::remove(path_, ec);
    }

    [[nodiscard]] const std::filesystem::path& path() const noexcept { return path_; }

private:
    std::filesystem::path path_;
};

raag::AstNode make_node(raag::AstNodeKind kind,
                        std::string name,
                        std::uint32_t byte_start,
                        std::uint32_t byte_end,
                        std::uint32_t first_child_index,
                        std::uint32_t child_count,
                        std::int32_t parent_index) {
    raag::AstNode node;
    node.kind = kind;
    node.name = std::move(name);
    node.byte_start = byte_start;
    node.byte_end = byte_end;
    node.first_child_index = first_child_index;
    node.child_count = child_count;
    node.parent_index = parent_index;
    return node;
}

/// A small arena with a root and two children, covering every field.
raag::AstArena make_sample_arena() {
    raag::AstArena arena;
    arena.add_node(make_node(raag::AstNodeKind::File, "", 0, 120, 1, 2, -1));
    arena.add_node(make_node(raag::AstNodeKind::Class, "Widget", 0, 60, 0, 0, 0));
    arena.add_node(make_node(raag::AstNodeKind::Function, "render", 61, 120, 0, 0, 0));
    return arena;
}

void expect_nodes_equal(const raag::AstNode& actual, const raag::AstNode& expected) {
    EXPECT_EQ(actual.kind, expected.kind);
    EXPECT_EQ(actual.name, expected.name);
    EXPECT_EQ(actual.byte_start, expected.byte_start);
    EXPECT_EQ(actual.byte_end, expected.byte_end);
    EXPECT_EQ(actual.first_child_index, expected.first_child_index);
    EXPECT_EQ(actual.child_count, expected.child_count);
    EXPECT_EQ(actual.parent_index, expected.parent_index);
}

}  // namespace

TEST(Snapshot, RoundTripsEveryNodeField) {
    const TempFile file("round_trip.bin");

    const raag::AstArena original = make_sample_arena();

    std::vector<std::pair<std::string, raag::AstArena>> entries;
    entries.emplace_back("src/widget.cpp", make_sample_arena());

    ASSERT_TRUE(raag::write_snapshot(file.path(), entries));

    const auto loaded = raag::read_snapshot(file.path());
    ASSERT_TRUE(loaded.has_value());
    ASSERT_EQ(loaded->size(), 1u);

    const raag::SnapshotEntry& entry = loaded->front();
    EXPECT_EQ(entry.path, "src/widget.cpp");
    ASSERT_EQ(entry.arena.size(), original.size());

    for (std::uint32_t i = 0; i < static_cast<std::uint32_t>(original.size()); ++i) {
        expect_nodes_equal(entry.arena.node(i), original.node(i));
    }
}

TEST(Snapshot, RoundTripsMultipleFileEntries) {
    const TempFile file("multi_entry.bin");

    std::vector<std::pair<std::string, raag::AstArena>> entries;
    entries.emplace_back("a.cpp", make_sample_arena());
    entries.emplace_back("nested/dir/b.py", make_sample_arena());
    entries.emplace_back("c.hpp", make_sample_arena());

    ASSERT_TRUE(raag::write_snapshot(file.path(), entries));

    const auto loaded = raag::read_snapshot(file.path());
    ASSERT_TRUE(loaded.has_value());
    ASSERT_EQ(loaded->size(), 3u);

    EXPECT_EQ((*loaded)[0].path, "a.cpp");
    EXPECT_EQ((*loaded)[1].path, "nested/dir/b.py");
    EXPECT_EQ((*loaded)[2].path, "c.hpp");

    for (const raag::SnapshotEntry& entry : *loaded) {
        EXPECT_EQ(entry.arena.size(), 3u);
    }
}

TEST(Snapshot, PreservesNegativeParentIndexOnRoot) {
    const TempFile file("negative_parent.bin");

    // -1 is the root sentinel. A serializer that widened the field through an
    // unsigned conversion would read it back as 4294967295.
    std::vector<std::pair<std::string, raag::AstArena>> entries;
    entries.emplace_back("root.cpp", make_sample_arena());

    ASSERT_TRUE(raag::write_snapshot(file.path(), entries));

    const auto loaded = raag::read_snapshot(file.path());
    ASSERT_TRUE(loaded.has_value());
    ASSERT_FALSE(loaded->empty());

    EXPECT_EQ(loaded->front().arena.node(0).parent_index, -1);
}

TEST(Snapshot, RoundTripsEmptyArena) {
    const TempFile file("empty_arena.bin");

    std::vector<std::pair<std::string, raag::AstArena>> entries;
    entries.emplace_back("empty.py", raag::AstArena{});

    ASSERT_TRUE(raag::write_snapshot(file.path(), entries));

    const auto loaded = raag::read_snapshot(file.path());
    ASSERT_TRUE(loaded.has_value());
    ASSERT_EQ(loaded->size(), 1u);
    EXPECT_TRUE(loaded->front().arena.empty());
}

TEST(Snapshot, RoundTripsSnapshotWithNoEntries) {
    const TempFile file("no_entries.bin");

    const std::vector<std::pair<std::string, raag::AstArena>> entries;
    ASSERT_TRUE(raag::write_snapshot(file.path(), entries));

    const auto loaded = raag::read_snapshot(file.path());
    ASSERT_TRUE(loaded.has_value());
    EXPECT_TRUE(loaded->empty());
}

TEST(Snapshot, PreservesUnicodeAndEmptyNames) {
    const TempFile file("unicode_names.bin");

    raag::AstArena arena;
    arena.add_node(make_node(raag::AstNodeKind::File, "", 0, 10, 1, 2, -1));
    arena.add_node(make_node(raag::AstNodeKind::Function, "\xC3\xA9t\xC3\xA9", 0, 5, 0, 0, 0));
    arena.add_node(make_node(raag::AstNodeKind::Variable, "", 6, 10, 0, 0, 0));

    std::vector<std::pair<std::string, raag::AstArena>> entries;
    entries.emplace_back("unicode.py", std::move(arena));

    ASSERT_TRUE(raag::write_snapshot(file.path(), entries));

    const auto loaded = raag::read_snapshot(file.path());
    ASSERT_TRUE(loaded.has_value());
    ASSERT_EQ(loaded->front().arena.size(), 3u);

    EXPECT_TRUE(loaded->front().arena.node(0).name.empty());
    EXPECT_EQ(loaded->front().arena.node(1).name, "\xC3\xA9t\xC3\xA9");
    EXPECT_TRUE(loaded->front().arena.node(2).name.empty());
}

// --- Failure handling --------------------------------------------------------

TEST(Snapshot, ReadingNonexistentFileFails) {
    const std::filesystem::path missing =
        std::filesystem::temp_directory_path() / "raag_definitely_not_here.bin";

    std::error_code ec;
    std::filesystem::remove(missing, ec);

    EXPECT_FALSE(raag::read_snapshot(missing).has_value());
}

TEST(Snapshot, ReadingWrongMagicBytesFails) {
    const TempFile file("bad_magic.bin");

    {
        std::ofstream out(file.path(), std::ios::binary);
        out << "NOPE";
        for (int i = 0; i < 32; ++i) {
            out.put('\0');
        }
    }

    EXPECT_FALSE(raag::read_snapshot(file.path()).has_value());
}

TEST(Snapshot, ReadingUnknownSchemaVersionFails) {
    const TempFile file("bad_version.bin");

    {
        // Correct magic, then a version far beyond anything this build knows.
        // Rejecting it is the entire purpose of writing a version field.
        std::ofstream out(file.path(), std::ios::binary);
        out.write(raag::kSnapshotMagic, 4);
        const char version[4] = {'\xFF', '\xFF', '\0', '\0'};
        out.write(version, 4);
        const char count[4] = {'\0', '\0', '\0', '\0'};
        out.write(count, 4);
    }

    EXPECT_FALSE(raag::read_snapshot(file.path()).has_value());
}

TEST(Snapshot, ReadingTruncatedFileFails) {
    const TempFile source("truncate_source.bin");
    const TempFile truncated("truncated.bin");

    std::vector<std::pair<std::string, raag::AstArena>> entries;
    entries.emplace_back("src/widget.cpp", make_sample_arena());
    ASSERT_TRUE(raag::write_snapshot(source.path(), entries));

    // Copy only the first 20 bytes: enough for a valid header, not enough for
    // the records it claims to contain. A reader that trusted the counts would
    // produce an arena with child ranges pointing past its own end.
    std::ifstream in(source.path(), std::ios::binary);
    ASSERT_TRUE(in.good());

    char buffer[20] = {};
    in.read(buffer, sizeof(buffer));
    const std::streamsize read_count = in.gcount();

    {
        std::ofstream out(truncated.path(), std::ios::binary);
        out.write(buffer, read_count);
    }

    EXPECT_FALSE(raag::read_snapshot(truncated.path()).has_value());
}

TEST(Snapshot, ReadingEmptyFileFails) {
    const TempFile file("zero_bytes.bin");

    {
        const std::ofstream out(file.path(), std::ios::binary);
    }

    EXPECT_FALSE(raag::read_snapshot(file.path()).has_value());
}

TEST(Snapshot, WriteCreatesMissingParentDirectories) {
    const std::filesystem::path directory =
        std::filesystem::temp_directory_path() / "raag_nested_test_dir";
    const std::filesystem::path target = directory / "deeper" / "snapshot.bin";

    std::error_code ec;
    std::filesystem::remove_all(directory, ec);

    std::vector<std::pair<std::string, raag::AstArena>> entries;
    entries.emplace_back("a.cpp", make_sample_arena());

    EXPECT_TRUE(raag::write_snapshot(target, entries));
    EXPECT_TRUE(std::filesystem::exists(target, ec));

    std::filesystem::remove_all(directory, ec);
}