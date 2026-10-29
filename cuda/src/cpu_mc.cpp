#include "optlab_cuda/cpu_mc.hpp"

#include <chrono>
#include <cmath>
#include <random>
#include <stdexcept>
#include <thread>
#include <vector>

#include "optlab_cuda/schemes.hpp"

namespace optlab {

Estimate estimate_from_sums(double sum, double sumsq, long long n_pairs) {
    const double n = static_cast<double>(n_pairs), mean = sum / n;
    const double var = sumsq / n - mean * mean;  // population variance of the pair averages
    return {mean, n_pairs > 1 ? std::sqrt(std::fmax(var, 0.0) / (n - 1.0)) : 0.0};
}

namespace {

struct StdNormals {
    std::mt19937_64 gen;
    std::normal_distribution<double> nd;
    void next4(double z[4]) {
        for (int k = 0; k < 4; ++k) z[k] = nd(gen);
    }
};

struct Sums {
    double s[kOutputs] = {}, s2[kOutputs] = {};
};

template <Model M, Product P>
void run_block(const PathConsts<double>& c, std::uint64_t seed, int block, long long n_pairs, Sums& acc) {
    std::seed_seq ss{static_cast<std::uint32_t>(seed), static_cast<std::uint32_t>(seed >> 32),
                     static_cast<std::uint32_t>(block)};
    StdNormals src{std::mt19937_64(ss), std::normal_distribution<double>()};
    double out[kOutputs];
    for (long long p = 0; p < n_pairs; ++p) {
        if (M == Model::BlackScholes && P == Product::European)
            bs_european_pair(c, src.nd(src.gen), out);
        else
            path_pair<double, M, P>(c, src, out);
        for (int k = 0; k < kOutputs; ++k) {
            acc.s[k] += out[k];
            acc.s2[k] += out[k] * out[k];
        }
    }
}

using BlockFn = void (*)(const PathConsts<double>&, std::uint64_t, int, long long, Sums&);

BlockFn pick(Model m, Product p) {
    if (m == Model::BlackScholes) {
        if (p == Product::European) return run_block<Model::BlackScholes, Product::European>;
        if (p == Product::Asian) return run_block<Model::BlackScholes, Product::Asian>;
        return run_block<Model::BlackScholes, Product::Barrier>;
    }
    if (p == Product::European) return run_block<Model::Heston, Product::European>;
    if (p == Product::Asian) return run_block<Model::Heston, Product::Asian>;
    return run_block<Model::Heston, Product::Barrier>;
}

}  // namespace

McResult cpu_price(const McRequest& rq, int n_threads) {
    if (rq.n_paths < 4 || rq.n_paths % 2) throw std::invalid_argument("n_paths must be even and >= 4");
    const auto t0 = std::chrono::steady_clock::now();
    const PathConsts<double> c = make_consts<double>(rq);
    const long long n_pairs = rq.n_paths / 2;
    const BlockFn fn = pick(rq.model, rq.product.product);
    std::vector<Sums> acc(n_threads);
    std::vector<std::thread> pool;
    for (int t = 0; t < n_threads; ++t) {
        const long long begin = n_pairs * t / n_threads, end = n_pairs * (t + 1) / n_threads;
        pool.emplace_back(fn, std::cref(c), rq.seed, t, end - begin, std::ref(acc[t]));
    }
    for (auto& th : pool) th.join();
    McResult res;
    res.n_paths = rq.n_paths;
    for (int k = 0; k < kOutputs; ++k) {
        double s = 0.0, s2 = 0.0;
        for (const Sums& a : acc) {
            s += a.s[k];
            s2 += a.s2[k];
        }
        res.out[k] = estimate_from_sums(s, s2, n_pairs);
    }
    res.kernel_ms = res.total_ms =
        std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    return res;
}

}  // namespace optlab
