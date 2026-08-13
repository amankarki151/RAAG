// =============================================================================
// RAAG — Thread pool tests
//
// Concurrency bugs are timing-dependent and do not reproduce reliably, so
// these tests use enough tasks that a broken implementation fails most runs
// rather than occasionally. A test that passes on a correct implementation but
// only sometimes fails on a broken one is still worth having; a test with too
// few tasks would pass on both.
// =============================================================================

#include <gtest/gtest.h>

#include <atomic>
#include <chrono>
#include <mutex>
#include <set>
#include <stdexcept>
#include <thread>
#include <vector>

#include "raag/thread_pool.hpp"

namespace {

constexpr int kTaskCount = 500;

}  // namespace

TEST(ThreadPool, RunsEverySubmittedTask) {
    std::atomic<int> counter{0};

    {
        raag::ThreadPool pool(4);
        for (int i = 0; i < kTaskCount; ++i) {
            pool.submit([&counter] { counter.fetch_add(1, std::memory_order_relaxed); });
        }
        pool.wait_for_completion();

        // Checked before the pool is destroyed. Asserting after destruction
        // would also pass if wait_for_completion did nothing and the
        // destructor happened to drain the queue.
        EXPECT_EQ(counter.load(), kTaskCount);
    }

    EXPECT_EQ(counter.load(), kTaskCount);
}

TEST(ThreadPool, WaitForCompletionBlocksUntilWorkFinishes) {
    std::atomic<int> completed{0};

    raag::ThreadPool pool(4);
    for (int i = 0; i < 50; ++i) {
        pool.submit([&completed] {
            // A short sleep makes the failure mode visible: an implementation
            // that returns as soon as the queue empties, rather than when all
            // running tasks finish, will observe a count below 50 here.
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
            completed.fetch_add(1, std::memory_order_relaxed);
        });
    }

    pool.wait_for_completion();
    EXPECT_EQ(completed.load(), 50);
}

TEST(ThreadPool, DefaultConstructionSelectsSomeThreads) {
    const raag::ThreadPool pool;
    EXPECT_GT(pool.thread_count(), 0u);
}

TEST(ThreadPool, ExplicitThreadCountIsHonoured) {
    const raag::ThreadPool pool(3);
    EXPECT_EQ(pool.thread_count(), 3u);
}

TEST(ThreadPool, ThrowingTaskDoesNotKillTheWorker) {
    std::atomic<int> succeeded{0};

    raag::ThreadPool pool(2);

    for (int i = 0; i < 100; ++i) {
        pool.submit([] { throw std::runtime_error("intentional test failure"); });
        pool.submit([&succeeded] { succeeded.fetch_add(1, std::memory_order_relaxed); });
    }

    pool.wait_for_completion();

    // If an escaping exception terminated a worker, the tasks queued behind it
    // would never run and this count would fall short.
    EXPECT_EQ(succeeded.load(), 100);
}

TEST(ThreadPool, DestructionWithPendingTasksDoesNotHang) {
    // The failure this guards against is a deadlock, which manifests as the
    // test suite never finishing rather than as a failed assertion.
    std::atomic<int> counter{0};

    {
        raag::ThreadPool pool(2);
        for (int i = 0; i < 200; ++i) {
            pool.submit([&counter] { counter.fetch_add(1, std::memory_order_relaxed); });
        }
        // Deliberately no wait_for_completion.
    }

    SUCCEED();
}

TEST(ThreadPool, WorkIsActuallyDistributedAcrossThreads) {
    std::mutex mutex;
    std::set<std::thread::id> observed_threads;

    {
        raag::ThreadPool pool(4);
        for (int i = 0; i < 400; ++i) {
            pool.submit([&mutex, &observed_threads] {
                // A brief hold keeps several tasks in flight simultaneously,
                // so the work cannot all be picked up by whichever worker
                // happens to wake first.
                std::this_thread::sleep_for(std::chrono::microseconds(200));
                const std::lock_guard<std::mutex> lock(mutex);
                observed_threads.insert(std::this_thread::get_id());
            });
        }
        pool.wait_for_completion();
    }

    // More than one worker participated. Asserting exactly four would be
    // flaky — the scheduler is under no obligation to use every thread.
    EXPECT_GT(observed_threads.size(), 1u);
}

TEST(ThreadPool, ConcurrentWritesToSharedStateAreComplete) {
    std::mutex mutex;
    std::vector<int> collected;

    {
        raag::ThreadPool pool(8);
        for (int i = 0; i < kTaskCount; ++i) {
            pool.submit([i, &mutex, &collected] {
                const std::lock_guard<std::mutex> lock(mutex);
                collected.push_back(i);
            });
        }
        pool.wait_for_completion();
    }

    ASSERT_EQ(collected.size(), static_cast<std::size_t>(kTaskCount));

    // Order is not guaranteed, but every value must appear exactly once.
    const std::set<int> unique(collected.begin(), collected.end());
    EXPECT_EQ(unique.size(), static_cast<std::size_t>(kTaskCount));
}

TEST(ThreadPool, WaitForCompletionOnIdlePoolReturnsImmediately) {
    raag::ThreadPool pool(2);
    pool.wait_for_completion();
    SUCCEED();
}

TEST(ThreadPool, RepeatedSubmitAndWaitCyclesWork) {
    std::atomic<int> counter{0};
    raag::ThreadPool pool(4);

    for (int round = 0; round < 5; ++round) {
        for (int i = 0; i < 100; ++i) {
            pool.submit([&counter] { counter.fetch_add(1, std::memory_order_relaxed); });
        }
        pool.wait_for_completion();
        EXPECT_EQ(counter.load(), (round + 1) * 100);
    }
}