add_executable(raag_sample_tests
    test_ast_builder.cpp
    test_thread_pool.cpp
    test_snapshot.cpp
)
target_link_libraries(raag_sample_tests PRIVATE raag_sample_core GTest::gtest_main)
include(GoogleTest)
gtest_discover_tests(raag_sample_tests)
