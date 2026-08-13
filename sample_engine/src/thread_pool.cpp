#include "raag/thread_pool.hpp"

#include <utility>

namespace raag {
namespace {

/// Used when hardware_concurrency() cannot determine a value. Four is a
/// conservative guess that keeps the pool useful on a typical machine without
/// oversubscribing a small one.
constexpr std::size_t kFallbackThreadCount = 4;

}  // namespace

ThreadPool::ThreadPool(std::size_t thread_count) {
    if (thread_count == 0) {
        const unsigned int detected = std::thread::hardware_concurrency();
        thread_count = (detected == 0) ? kFallbackThreadCount
                                       : static_cast<std::size_t>(detected);
    }

    workers_.reserve(thread_count);
    for (std::size_t i = 0; i < thread_count; ++i) {
        // A jthread invocable taking std::stop_token as its first parameter
        // receives the thread's own token automatically.
        workers_.emplace_back(
            [this](std::stop_token stop) { worker_loop(std::move(stop)); });
    }
}

ThreadPool::~ThreadPool() {
    for (std::jthread& worker : workers_) {
        worker.request_stop();
    }

    // condition_variable_any registers a stop callback that wakes waiters on
    // its own, so this notify is belt-and-braces rather than strictly
    // required. It costs nothing and makes the wake-up path obvious.
    task_available_.notify_all();

    // No join loop: ~jthread joins. Workers finish before workers_ is fully
    // destroyed, and workers_ is destroyed before mutex_ and the condition
    // variables, because it is declared last in the class.
}

void ThreadPool::submit(std::function<void()> task) {
    {
        const std::lock_guard<std::mutex> lock(mutex_);
        tasks_.push(std::move(task));
        ++outstanding_tasks_;
    }

    // Notified outside the lock so the woken worker does not immediately block
    // trying to acquire a mutex this thread still holds.
    task_available_.notify_one();
}

void ThreadPool::wait_for_completion() {
    std::unique_lock<std::mutex> lock(mutex_);
    all_tasks_done_.wait(lock, [this] { return outstanding_tasks_ == 0; });
}

void ThreadPool::worker_loop(std::stop_token stop) {
    while (true) {
        std::function<void()> task;

        {
            std::unique_lock<std::mutex> lock(mutex_);

            // Returns false only when stop was requested while the predicate
            // was still false. A stop request with work still queued lets the
            // queue drain rather than discarding tasks mid-run.
            task_available_.wait(lock, stop, [this] { return !tasks_.empty(); });

            if (tasks_.empty()) {
                return;  // Stop requested and nothing left to do.
            }

            task = std::move(tasks_.front());
            tasks_.pop();
        }

        // A task is arbitrary user code. Letting an exception escape here
        // would call std::terminate and take the whole process down because
        // of one unparseable file. The pool absorbs it and carries on; the
        // caller is responsible for recording per-task failures itself.
        try {
            task();
        } catch (...) {  // NOLINT(bugprone-empty-catch)
        }

        {
            const std::lock_guard<std::mutex> lock(mutex_);
            --outstanding_tasks_;
            if (outstanding_tasks_ == 0) {
                all_tasks_done_.notify_all();
            }
        }
    }
}

}  // namespace raag