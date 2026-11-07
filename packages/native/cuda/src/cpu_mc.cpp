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
    double next1() { return nd(gen); }
    void next4(double z[4]) {
        for (int k = 0; k < 4; ++k) z[k] = nd(gen);
    }
};

struct BoxMullerNormals {
    std::mt19937_64 gen;
    double spare = 0.0;
    bool has_spare = false;
    double uniform() { return (static_cast<double>(gen() >> 11) + 0.5) * 0x1.0p-53; }  // in (0, 1)
    void pair(double& a, double& b) {
        const double r = std::sqrt(-2.0 * std::log(uniform())), t = 6.283185307179586 * uniform();
        a = r * std::cos(t);
        b = r * std::sin(t);
    }
    double next1() {
        if (has_spare) {
            has_spare = false;
            return spare;
        }
        double a;
        pair(a, spare);
        has_spare = true;
        return a;
    }
    void next4(double z[4]) {
        pair(z[0], z[1]);
        pair(z[2], z[3]);
    }
};

struct Sums {
    double s[kOutputs] = {}, s2[kOutputs] = {};
};

template <Model M, Product P, typename Src>
void run_block(const PathConsts<double>& c, std::uint64_t seed, int block, long long n_pairs, Sums& result) {
    Sums acc;  // local, written back once: the per-thread results share cache lines (false sharing)
    std::seed_seq ss{static_cast<std::uint32_t>(seed), static_cast<std::uint32_t>(seed >> 32),
                     static_cast<std::uint32_t>(block)};
    Src src{std::mt19937_64(ss)};
    double out[kOutputs];
    for (long long p = 0; p < n_pairs; ++p) {
        if constexpr (M == Model::BlackScholes && P == Product::European)
            bs_european_unit(c, src.next1(), out);
        else
            path_unit<double, M, P>(c, src, out);
        for (int k = 0; k < kOutputs; ++k) {
            acc.s[k] += out[k];
            acc.s2[k] += out[k] * out[k];
        }
    }
    result = acc;
}

using BlockFn = void (*)(const PathConsts<double>&, std::uint64_t, int, long long, Sums&);

template <typename Src>
BlockFn pick(Model m, Product p) {
    if (m == Model::BlackScholes) {
        if (p == Product::European) return run_block<Model::BlackScholes, Product::European, Src>;
        if (p == Product::Asian) return run_block<Model::BlackScholes, Product::Asian, Src>;
        return run_block<Model::BlackScholes, Product::Barrier, Src>;
    }
    if (p == Product::European) return run_block<Model::Heston, Product::European, Src>;
    if (p == Product::Asian) return run_block<Model::Heston, Product::Asian, Src>;
    return run_block<Model::Heston, Product::Barrier, Src>;
}

}  // namespace

McResult cpu_price(const McRequest& rq, int n_threads, CpuNormals normals) {
    if (rq.n_paths < 4 || rq.n_paths % 2) throw std::invalid_argument("n_paths must be even and >= 4");
    const auto t0 = std::chrono::steady_clock::now();
    const PathConsts<double> c = make_consts<double>(rq);
    const long long n_pairs = rq.n_paths / 2;
    const BlockFn fn = normals == CpuNormals::Std ? pick<StdNormals>(rq.model, rq.product.product)
                                                  : pick<BoxMullerNormals>(rq.model, rq.product.product);
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
