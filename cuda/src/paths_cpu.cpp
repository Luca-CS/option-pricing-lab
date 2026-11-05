// Path engine on the CPU: pairs handed out in blocks to every hardware thread, in double precision.
#include <algorithm>
#include <atomic>
#include <chrono>
#include <thread>
#include <vector>

#include "optlab_cuda/paths_engine.hpp"

namespace optlab {

namespace {

template <int N>
void run_block(const PathSpec<double>& s, long long a, long long b) {
    for (long long p = a; p < b; ++p) simulate_pair<double, N>(s, p);
}

using BlockFn = void (*)(const PathSpec<double>&, long long, long long);

BlockFn pick(int n_names) {
    if (n_names <= 1) return run_block<1>;
    if (n_names <= 2) return run_block<2>;
    if (n_names <= 4) return run_block<4>;
    return run_block<kMaxNames>;
}

}  // namespace

PathTiming cpu_paths(const PathSpec<double>& s, int n_threads) {
    const auto t0 = std::chrono::steady_clock::now();
    const int hw = static_cast<int>(std::thread::hardware_concurrency());
    int threads = n_threads > 0 ? n_threads : (hw > 0 ? hw : 1);
    const long long block = 512;
    const long long n_blocks = (s.n_pairs + block - 1) / block;
    threads = static_cast<int>(std::min<long long>(threads, std::max<long long>(n_blocks, 1)));
    const BlockFn fn = pick(s.n_names);
    std::atomic<long long> next{0};
    auto work = [&]() {
        for (long long k; (k = next.fetch_add(1)) < n_blocks;) fn(s, k * block, std::min(s.n_pairs, (k + 1) * block));
    };
    if (threads == 1) {
        work();
    } else {
        std::vector<std::thread> pool;
        pool.reserve(threads);
        for (int t = 0; t < threads; ++t) pool.emplace_back(work);
        for (auto& th : pool) th.join();
    }
    const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    return {ms, ms};
}

}  // namespace optlab
