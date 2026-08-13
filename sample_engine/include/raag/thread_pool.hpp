#pragma once

// =============================================================================
// RAAG — Worker pool
//
// Parsing is embarrassingly parallel: files are independent, so throughput
// scales with core count until I/O saturates. This pool distributes per-file
// parse jobs across hardware threads.
//
// Built on std::jthread rather than std::thread for two properties that matter
// here:
//
//   1. Automatic joining on destruction. There is no manual join loop, and no
//      way to leak a detached thread by taking an early return out of the
//      destructor.
//   2. A built-in stop_token, so an interrupted run terminates cleanly instead
//      of leaving workers blocked on a condition variable forever.
// =============================================================================

#include <condition_variable>
#include <cstddef>
#include <functional>
#include <mutex>
#include <queue>
#include <stop_token>
#include <thread>
#include <vector>

namespace raag {

class ThreadPool {
public:
    /// Constructs a pool with `thread_count` workers.
    ///
    /// Passing 0 selects std::thread::hardware_concurrency(), which is allowed
    /// to return 0 when the implementation cannot determine a value; a fixed
    /// fallback is used in that case rather than spawning zero workers and
    /// deadlocking on the first wait_for_completion().
    explicit ThreadPool(std::size_t thread_count = 0);

    /// Requests stop on every worker. The std::jthread members then join
    /// themselves as they are destroyed.
    ~ThreadPool();

    // A pool owns running threads that capture `this`. Copying would duplicate
    // the queue while the copies' workers still referenced the original;
    // moving would leave those workers pointing at a moved-from object. Both
    // are deleted rather than left to produce a runtime surprise.
    ThreadPool(const ThreadPool&) = delete;
    ThreadPool& operator=(const ThreadPool&) = delete;
    ThreadPool(ThreadPool&&) = delete;
    ThreadPool& operator=(ThreadPool&&) = delete;

    /// Enqueues a task. Returns immediately; the task runs on a worker.
    void submit(std::function<void()> task);

    /// Blocks until every task submitted so far has finished executing.
    ///
    /// Counts outstanding tasks rather than checking whether the queue is
    /// empty: a task that has been dequeued but is still running would
    /// otherwise be missed, and this would return while work was in flight.
    void wait_for_completion();

    [[nodiscard]] std::size_t thread_count() const noexcept { return workers_.size(); }

private:
    void worker_loop(std::stop_token stop);

    mutable std::mutex mutex_;

    // std::condition_variable_any rather than std::condition_variable: only
    // the _any variant accepts a stop_token in wait(). Without it, a worker
    // blocked on an empty queue would never observe a stop request and the
    // destructor would hang.
    std::condition_variable_any task_available_;
    std::condition_variable_any all_tasks_done_;

    std::queue<std::function<void()>> tasks_;
    std::size_t outstanding_tasks_{0};

    // Declared last, and therefore destroyed first. Each jthread destructor
    // requests stop and joins, so all workers have exited before the mutex and
    // condition variables they use are destroyed. Reordering this member is a
    // use-after-free.
    std::vector<std::jthread> workers_;
};

}  // namespace raag