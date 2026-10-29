// Optimisation study (handoff, M6): one change at a time against the production kernel, same protocol as bench.cpp
// (sustained warm-up, then per row at least 300 ms of warm-up and 5 repetitions, median [min, max]), and every row
// validated against its reference. Rows whose normals are those of the baseline also report the price gap to the
// baseline in standard errors: that gap is pure arithmetic.
//
//   build\Release\ablation.exe            GPU variants, stored paths, CPU normal generators
//   build\Release\ablation_fastmath.exe   baseline rows of a library compiled with --use_fast_math
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <string>
#include <thread>
#include <vector>

#include "optlab_cuda/cpu_mc.hpp"
#include "optlab_cuda/gpu_mc.cuh"

using namespace optlab;

namespace {

constexpr int kReps = 5;

struct Stats {
    double med, lo, hi;
};

Stats stats(std::vector<double> v) {
    std::sort(v.begin(), v.end());
    return {v[v.size() / 2], v.front(), v.back()};
}

struct Ref {
    const char* id;
    double value, se;
};

McRequest request(Model m, Product p, int steps, long long n) {
    McRequest rq;
    rq.model = m;
    rq.product.product = p;
    rq.product.legs = {Leg::Call, Leg::None, Leg::None};
    rq.product.strikes = {100, 100, 100};
    rq.product.H = 80;
    rq.product.n_steps = steps;
    rq.n_paths = n;
    return rq;
}

struct Measured {
    Stats kernel, total;
    GpuLaunchInfo info;
    McResult res;
};

Measured measure(const McRequest& rq, const GpuConfig& cfg, bool stored = false) {
    auto call = [&](GpuLaunchInfo* info) { return stored ? gpu_price_stored(rq, cfg, info) : gpu_price(rq, cfg, info); };
    const auto t0 = std::chrono::steady_clock::now();
    do call(nullptr);
    while (std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count() < 300.0);
    Measured m;
    std::vector<double> k, t;
    for (int r = 0; r < kReps; ++r) {
        m.res = call(&m.info);
        k.push_back(m.res.kernel_ms);
        t.push_back(m.res.total_ms);
    }
    m.kernel = stats(k);
    m.total = stats(t);
    return m;
}

int g_failures = 0;

void header() {
    std::printf("| Variant | Kernel (ms) median [min, max] | End to end (ms) | Path-steps/s | Price | SE | vs ref (SE) | "
                "Gap to baseline (SE) | Regs | Blocks/SM x block (occupancy) | Efficiency 1/(t SE^2) | Validation |\n"
                "|---|---|---|---|---|---|---|---|---|---|---|---|\n");
}

void row(const char* name, const Measured& m, long long n, int steps, const Ref& ref, const McResult* base,
         bool same_normals) {
    const Estimate& e = m.res.out[0];
    const double comb = std::sqrt(e.se * e.se + ref.se * ref.se), dev = e.price - ref.value;
    const bool ok = std::fabs(dev) <= 4 * comb;
    if (!ok) ++g_failures;
    char gap[32] = "-";
    if (base && same_normals) std::snprintf(gap, sizeof gap, "%+.4f", (e.price - base->out[0].price) / e.se);
    const double t = m.kernel.med / 1e3;
    std::printf("| %s | %.3f [%.3f, %.3f] | %.3f | %.3e | %.6f | %.6f | %+.2f | %s | %d | %d x %d (%.0f %%) | %.3e | %s |\n",
                name, m.kernel.med, m.kernel.lo, m.kernel.hi, m.total.med, n * static_cast<double>(steps) / t,
                e.price, e.se, dev / comb, gap, m.info.registers, m.info.blocks_per_sm, m.info.block,
                100.0 * m.info.blocks_per_sm * m.info.block / 1024.0, 1.0 / (t * e.se * e.se),
                ok ? "PASS" : "FAIL");
    std::fflush(stdout);
}

void study(const char* title, const McRequest& rq, const Ref& ref, bool fastmath_build, const char* label) {
    const int steps = rq.product.n_steps;
    std::printf("\n### %s, N = %lld, %d step(s), reference %s = %.6f +- %.6f\n\n", title, rq.n_paths, steps, ref.id,
                ref.value, ref.se);
    header();
    const Measured base = measure(rq, {});
    row(fastmath_build ? label : "baseline (production defaults)",
        base, rq.n_paths, steps, ref, nullptr, false);
    if (fastmath_build) return;

    auto variant = [&](const char* name, GpuConfig cfg, bool same_normals) {
        row(name, measure(rq, cfg), rq.n_paths, steps, ref, &base.res, same_normals);
    };
    GpuConfig c;
    c.use_double = true;
    variant("1. double path state (same normals)", c, true);
    c = {};
    c.variant.fast_intrinsics = true;
    variant("2. __expf / __logf intrinsics", c, true);
    c = {};
    c.variant.normal1 = true;
    variant("3. curand_normal x4 instead of curand_normal4", c, false);
    for (int b : {128, 256}) {
        c = {};
        c.block = b;
        const std::string name = "4. block " + std::to_string(b);
        variant(name.c_str(), c, true);
    }
    c = {};
    c.variant.min_blocks4 = true;
    c.block = 256;
    variant("4. block 256, __launch_bounds__(256, 4)", c, true);
    c = {};
    c.variant.antithetic = false;
    variant("6. no antithetic (same N paths)", c, false);
    c = {};
    c.variant.double_sums = true;
    variant("7. double sums instead of Kahan float", c, true);
    c = {};
    c.variant.per_thread_rng = true;
    variant("8. Philox state per thread, initialised once", c, false);
}

void stored(const char* title, const McRequest& rq, const Ref& ref) {
    std::printf("\n### 5. %s, N = %lld x %d steps: fused kernel against stored paths (%.2f GB of paths)\n\n", title,
                rq.n_paths, rq.product.n_steps, rq.n_paths * rq.product.n_steps * 4.0 / 1e9);
    header();
    const Measured fused = measure(rq, {});
    row("fused (payoff in the path kernel)", fused, rq.n_paths, rq.product.n_steps, ref, nullptr, false);
    const Measured st = measure(rq, {}, true);
    row("stored: path kernel + payoff kernel", st, rq.n_paths, rq.product.n_steps, ref, &fused.res, true);
    const double t_pay = st.info.kernel2_ms, t_paths = st.kernel.med - t_pay;
    const double gb = 2.0 * rq.n_paths * rq.product.n_steps * 4.0 / 1e9;  // written once, read once
    std::printf("\nStored: path kernel %.3f ms, payoff kernel %.3f ms (last repetition); %.2f GB moved -> %.0f GB/s "
                "effective over the two kernels (card peak 448 GB/s)\n",
                t_paths, t_pay, gb, gb / (st.kernel.med / 1e3));
}

void cpu_normals() {
    const int threads = static_cast<int>(std::thread::hardware_concurrency());
    std::printf("\n### 9. CPU normal generator (C++ reference, double): std::normal_distribution against Box-Muller\n\n"
                "| Case | N | Threads | Normals | Time (s) median [min, max] | Normals/s | Price | SE | vs ref (SE) |\n"
                "|---|---|---|---|---|---|---|---|---|\n");
    struct C {
        const char* name;
        McRequest rq;
        Ref ref;
        double normals_per_path;
    } cases[] = {
        {"BS barrier", request(Model::BlackScholes, Product::Barrier, 252, 1'000'000), {"R6", 9.245769, 0.007422}, 126},
        {"Heston barrier", request(Model::Heston, Product::Barrier, 252, 200'000), {"R9", 8.0927, 0.0092}, 252},
    };
    for (const C& c : cases)
        for (int th : {1, threads})
            for (CpuNormals nm : {CpuNormals::Std, CpuNormals::BoxMuller}) {
                std::vector<double> t;
                McResult r;
                for (int k = 0; k < kReps; ++k) {
                    r = cpu_price(c.rq, th, nm);
                    t.push_back(r.total_ms / 1e3);
                }
                const Stats s = stats(t);
                const Estimate& e = r.out[0];
                const double comb = std::sqrt(e.se * e.se + c.ref.se * c.ref.se), dev = e.price - c.ref.value;
                if (std::fabs(dev) > 4 * comb) ++g_failures;
                std::printf("| %s | %lld | %d | %s | %.3f [%.3f, %.3f] | %.3e | %.6f | %.6f | %+.2f |\n", c.name,
                            c.rq.n_paths, th, nm == CpuNormals::Std ? "std::normal_distribution" : "Box-Muller",
                            s.med, s.lo, s.hi, c.rq.n_paths * c.normals_per_path / s.med, e.price, e.se, dev / comb);
                std::fflush(stdout);
            }
}

}  // namespace

int main() {
#ifdef OPTLAB_BUILD_KIND
    constexpr bool fastmath = true;  // library compiled with other math flags: baseline rows only
    const char* label = OPTLAB_BUILD_KIND == 1 ? "--use_fast_math (whole library)"
                                               : "IEEE division and sqrt, no flush to zero (nvcc defaults)";
#else
    constexpr bool fastmath = false;
    const char* label = "standard";
#endif
    std::printf("%s, %s build\n", gpu_device_name().c_str(), label);
    const auto t0 = std::chrono::steady_clock::now();  // sustained warm-up: the boost clock takes a while
    do gpu_price(request(Model::BlackScholes, Product::Barrier, 252, 10'000'000));
    while (std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count() < 2.0);

    study("Heston QE down-and-out call", request(Model::Heston, Product::Barrier, 252, 10'000'000),
          {"R9", 8.0927, 0.0092}, fastmath, label);
    study("Black-Scholes European call", request(Model::BlackScholes, Product::European, 1, 100'000'000),
          {"R1", 9.319738, 0.0}, fastmath, label);
    if (fastmath)
        study("Black-Scholes down-and-out call", request(Model::BlackScholes, Product::Barrier, 252, 10'000'000),
              {"R6", 9.245769, 0.007422}, fastmath, label);
    if (!fastmath) {
        stored("Heston QE down-and-out call", request(Model::Heston, Product::Barrier, 252, 2'000'000),
               {"R9", 8.0927, 0.0092});
        stored("Black-Scholes down-and-out call", request(Model::BlackScholes, Product::Barrier, 252, 2'000'000),
               {"R6", 9.245769, 0.007422});
        cpu_normals();
    }
    std::printf("\n%s: %d validation failure(s)\n", g_failures ? "FAILED" : "OK", g_failures);
    return g_failures ? 1 : 0;
}
