// Benchmark protocol of the handoff (section 6). Run from the repository root, after cuda/bench/bench_numpy.py:
//
//   build\Release\bench.exe            writes cuda/bench/results.csv and prints the Markdown table
//
// GPU: one untimed warm-up call per row (module loading, caches), then 5 repetitions; kernel time from CUDA
// events (sum over the launches of one call), end-to-end time from steady_clock (allocation, launches, copy).
// CPU: same 5 repetitions, N capped so that one repetition takes about kCpuBudget seconds; the time for the
// requested N is then extrapolated linearly and flagged with '*'.
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <ctime>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#ifdef _MSC_VER
#include <intrin.h>
#endif
#include <cuda_runtime.h>

#include "optlab_cuda/cpu_mc.hpp"
#include "optlab_cuda/gpu_mc.cuh"

using namespace optlab;

namespace {

constexpr int kReps = 5;
constexpr double kCpuBudget = 6.0;  // seconds per CPU repetition

struct Stats {
    double med, lo, hi;
};

Stats stats(std::vector<double> v) {
    std::sort(v.begin(), v.end());
    return {v[v.size() / 2], v.front(), v.back()};
}

std::string cpu_name() {
#ifdef _MSC_VER
    int regs[12];
    __cpuid(regs, 0x80000002);
    __cpuid(regs + 4, 0x80000003);
    __cpuid(regs + 8, 0x80000004);
    std::string s(reinterpret_cast<char*>(regs), sizeof(regs));
    s = s.c_str();
    while (!s.empty() && s.back() == ' ') s.pop_back();
    return s;
#else
    return "unknown CPU";
#endif
}

struct Case {
    const char* name;
    Model model;
    Product product;
    int steps;
    std::vector<long long> sizes;
};

McRequest request(const Case& c, long long n) {
    McRequest rq;
    rq.model = c.model;
    rq.product.product = c.product;
    rq.product.legs = {Leg::Call, Leg::None, Leg::None};
    rq.product.strikes = {100, 100, 100};
    rq.product.H = 80;
    rq.product.n_steps = c.steps;
    rq.n_paths = n;
    return rq;
}

struct CpuTiming {
    long long n_measured;
    Stats s;
    double time_for_n;  // seconds, extrapolated if n_measured < n
};

CpuTiming time_cpu(const Case& c, long long n, int threads, double rate) {
    long long cap = std::min<long long>(n, static_cast<long long>(rate * kCpuBudget));
    cap -= cap % 2;
    cap = std::max<long long>(cap, 4);
    std::vector<double> t;
    for (int k = 0; k < kReps; ++k) t.push_back(cpu_price(request(c, cap), threads).total_ms / 1e3);
    const Stats s = stats(t);
    return {cap, s, s.med * static_cast<double>(n) / cap};
}

double cpu_rate(const Case& c, int threads) {  // paths per second, from a short probe
    const long long probe = c.steps == 1 ? 2'000'000 : (c.model == Model::Heston ? 20'000 : 40'000);
    cpu_price(request(c, probe), threads);
    return probe / (cpu_price(request(c, probe), threads).total_ms / 1e3);
}

std::map<std::pair<std::string, long long>, std::pair<double, bool>> read_numpy(const std::string& path) {
    std::map<std::pair<std::string, long long>, std::pair<double, bool>> m;
    std::ifstream f(path);
    std::string line;
    std::getline(f, line);  // header: product,n_requested,n_measured,python_grid_steps,median_s,min_s,max_s,
                            //         time_for_n_s,extrapolated,price,stderr
    while (std::getline(f, line)) {
        std::vector<std::string> col;
        std::stringstream ss(line);
        for (std::string x; std::getline(ss, x, ',');) col.push_back(x);
        if (col.size() < 9) continue;
        m[{col[0], std::stoll(col[1])}] = {std::stod(col[7]), col[8] == "True"};
    }
    return m;
}

// Repeats the call for at least `ms` of wall time, so that the GPU reaches its boost clock: one short call
// after seconds of CPU work runs at the idle clock (300 MHz on this card) and times the clock ramp, not the kernel.
void gpu_warm(const McRequest& rq, double ms) {
    const auto t0 = std::chrono::steady_clock::now();
    do gpu_price(rq);
    while (std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count() < ms);
}

void stamp(const char* what) {
    const std::time_t t = std::time(nullptr);
    char b[32];
    std::strftime(b, sizeof b, "%H:%M:%S", std::localtime(&t));
    std::printf("[%s] %s\n", b, what);
    std::fflush(stdout);
}

struct GpuRow {
    Stats kernel, total;
    GpuLaunchInfo info;
    McResult last;
};

std::string fmt_n(long long n) {
    int e = 0;
    long long m = n;
    while (m % 10 == 0 && m > 9) m /= 10, ++e;
    char b[32];
    if (m == 1) std::snprintf(b, sizeof b, "10^%d", e);
    else std::snprintf(b, sizeof b, "%lldx10^%d", m, e);
    return b;
}

}  // namespace

int main() {
    int drv = 0, rt = 0;
    cudaDriverGetVersion(&drv);
    cudaRuntimeGetVersion(&rt);
    const int threads = static_cast<int>(std::thread::hardware_concurrency());
    std::printf("GPU %s, driver API %d, runtime %d\nCPU %s, %d hardware threads\nMSVC %d\n\n",
                gpu_device_name().c_str(), drv, rt, cpu_name().c_str(), threads, _MSC_FULL_VER);

    // Start-up cost: the first call creates the CUDA context and loads the module.
    const Case euro{"BS European", Model::BlackScholes, Product::European, 1, {1'000'000, 10'000'000, 100'000'000}};
    const McResult first = gpu_price(request(euro, 1'000'000));
    const McResult second = gpu_price(request(euro, 1'000'000));
    std::printf("Start-up: first call %.1f ms end to end (kernel %.3f ms); second call %.2f ms (kernel %.3f ms)\n\n",
                first.total_ms, first.kernel_ms, second.total_ms, second.kernel_ms);

    const std::vector<Case> cases = {
        euro,
        {"BS barrier", Model::BlackScholes, Product::Barrier, 252, {1'000'000, 10'000'000}},
        {"Heston European", Model::Heston, Product::European, 252, {100'000, 1'000'000, 10'000'000}},
        {"Heston barrier", Model::Heston, Product::Barrier, 252, {100'000, 1'000'000, 10'000'000}},
    };
    const auto numpy = read_numpy("cuda/bench/numpy_results.csv");

    std::ofstream csv("cuda/bench/results.csv");
    csv << "product,n,steps,gpu_kernel_ms_median,gpu_kernel_ms_min,gpu_kernel_ms_max,gpu_total_ms_median,"
           "gpu_launches,gpu_grid,gpu_block,gpu_price,gpu_se,cpu1_n_measured,cpu1_s_median,cpu1_s_min,cpu1_s_max,"
           "cpu1_s_for_n,cpu12_n_measured,cpu12_s_median,cpu12_s_min,cpu12_s_max,cpu12_s_for_n,numpy_s_for_n,"
           "numpy_extrapolated,speedup_vs_cpu1,speedup_vs_cpu12,gpu_path_steps_per_s\n";
    std::vector<std::string> md;

    // Phase 1: every GPU measurement, back to back, so the clock stays at boost.
    stamp("GPU phase: sustained warm-up (2 s)");
    gpu_warm(request(cases[1], 10'000'000), 2000.0);
    std::map<std::pair<std::string, long long>, GpuRow> gpu;
    for (const Case& c : cases) {
        for (long long n : c.sizes) {
            const McRequest rq = request(c, n);
            gpu_warm(rq, 300.0);
            GpuRow row;
            std::vector<double> kms, tms;
            for (int k = 0; k < kReps; ++k) {
                row.last = gpu_price(rq, {}, &row.info);
                kms.push_back(row.last.kernel_ms);
                tms.push_back(row.last.total_ms);
            }
            row.kernel = stats(kms);
            row.total = stats(tms);
            std::printf("%-16s N %-9s gpu kernel %9.3f ms [%.3f, %.3f], e2e %9.3f ms, %d launch(es) %dx%d, "
                        "price %.5f +- %.5f\n", c.name, fmt_n(n).c_str(), row.kernel.med, row.kernel.lo,
                        row.kernel.hi, row.total.med, row.info.launches, row.info.grid, row.info.block,
                        row.last.out[0].price, row.last.out[0].se);
            gpu[{c.name, n}] = row;
        }
    }
    stamp("GPU phase done; CPU phase");

    // Phase 2: CPU, single thread and all hardware threads.
    for (const Case& c : cases) {
        const double rate1 = cpu_rate(c, 1), rate12 = cpu_rate(c, threads);
        for (long long n : c.sizes) {
            const GpuRow& gr = gpu.at({c.name, n});
            const Stats &ks = gr.kernel, &ts = gr.total;
            const GpuLaunchInfo& info = gr.info;
            const McResult& g = gr.last;
            const CpuTiming c1 = time_cpu(c, n, 1, rate1), c12 = time_cpu(c, n, threads, rate12);
            const auto np = numpy.find({c.name, n});
            const double path_steps = static_cast<double>(n) * c.steps / (ks.med / 1e3);
            const double sp1 = c1.time_for_n * 1e3 / ks.med, sp12 = c12.time_for_n * 1e3 / ks.med;

            std::printf("%-16s N %-9s cpu 1T %.3f s at N %lld [%.3f, %.3f] -> %.3f s;  cpu %dT %.3f s at N %lld -> %.3f s\n",
                        c.name, fmt_n(n).c_str(), c1.s.med, c1.n_measured, c1.s.lo, c1.s.hi, c1.time_for_n, threads, c12.s.med,
                        c12.n_measured, c12.time_for_n);
            std::fflush(stdout);

            char line[1024];
            std::snprintf(line, sizeof line,
                          "%s,%lld,%d,%.4f,%.4f,%.4f,%.4f,%d,%d,%d,%.6f,%.6f,%lld,%.4f,%.4f,%.4f,%.4f,%lld,%.4f,%.4f,"
                          "%.4f,%.4f,%s,%s,%.1f,%.1f,%.4e\n",
                          c.name, n, c.steps, ks.med, ks.lo, ks.hi, ts.med, info.launches, info.grid, info.block,
                          g.out[0].price, g.out[0].se, c1.n_measured, c1.s.med, c1.s.lo, c1.s.hi, c1.time_for_n,
                          c12.n_measured, c12.s.med, c12.s.lo, c12.s.hi, c12.time_for_n,
                          np != numpy.end() ? std::to_string(np->second.first).c_str() : "",
                          np != numpy.end() ? (np->second.second ? "1" : "0") : "", sp1, sp12, path_steps);
            csv << line;

            char np_cell[32] = "n/a";
            if (np != numpy.end())
                std::snprintf(np_cell, sizeof np_cell, "%.2f%s", np->second.first, np->second.second ? "*" : "");
            std::snprintf(line, sizeof line,
                          "| %s | %s | %d | %.2f%s | %.3f%s | %s | %.2f [%.2f, %.2f] | %.2f | %.0f | %.0f | %.2e |",
                          c.name, fmt_n(n).c_str(), c.steps, c1.time_for_n, c1.n_measured < n ? "*" : "",
                          c12.time_for_n, c12.n_measured < n ? "*" : "", np_cell, ks.med, ks.lo, ks.hi, ts.med, sp1,
                          sp12, path_steps);
            md.push_back(line);
        }
    }

    std::printf("\n| Product | N | Steps | CPU C++ 1 thread (s) | CPU C++ %d threads (s) | NumPy, optlab (s) | "
                "GPU kernel (ms) median [min, max] | GPU end to end (ms) | Speed-up kernel / C++ 1T | "
                "Speed-up kernel / C++ %dT | GPU path-steps/s |\n|---|---|---|---|---|---|---|---|---|---|---|\n",
                threads, threads);
    for (const std::string& l : md) std::printf("%s\n", l.c_str());
    std::printf("\n* measured at a smaller N (see results.csv) and extrapolated linearly.\n");
    return 0;
}
