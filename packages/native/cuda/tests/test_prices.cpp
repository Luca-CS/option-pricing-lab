// Validation against the reference table of the handoff (section 5).
// Criterion: |price - ref| <= 4 sqrt(se^2 + se_ref^2) + allowed bias. Rows marked "info" are reported, not tested.
//
//   test_prices              closed forms + CPU reference + GPU float + GPU double (float vs double, same normals)
//   test_prices cpu|gpu|double|closed
//   test_prices qe-bias      Heston QE daily bias against Fourier with 10^8 paths (GPU)
#include <cmath>
#include <cstdio>
#include <cstring>
#include <functional>
#include <string>
#include <thread>
#include <vector>

#include "optlab_cuda/closed_forms.hpp"
#include "optlab_cuda/cpu_mc.hpp"
#include "optlab_cuda/gpu_mc.cuh"

using namespace optlab;

namespace {

const double kMartingale = 100.0 * std::exp(0.03);  // E[S_T] = S0 exp((r - q) T)

struct Check {
    const char* id;
    const char* name;
    int output;
    double ref, se_ref, bias;
    bool info = false;
};

struct Group {
    const char* name;
    McRequest rq;
    std::vector<Check> checks;
};

int g_failures = 0;

McRequest request(Model m, Product p, std::array<Leg, 3> legs, std::array<double, 3> strikes, int n_steps) {
    McRequest rq;
    rq.model = m;
    rq.product.product = p;
    rq.product.legs = legs;
    rq.product.strikes = strikes;
    rq.product.n_steps = n_steps;
    return rq;
}

std::vector<Group> groups(long long n_bs, long long n_heston) {
    const auto C = Leg::Call, P = Leg::Put, F = Leg::Forward, N = Leg::None;
    std::vector<Group> g;
    g.push_back({"BS European", request(Model::BlackScholes, Product::European, {C, P, F}, {100, 100, 100}, 1),
                 {{"R1", "BS call K 100", 0, 9.319738, 0, 0},
                  {"R2", "BS put K 100", 1, 6.393699, 0, 0},
                  {"PCP", "BS call - put, same paths", 2, 2.926039, 0, 0},
                  {"MG", "BS E[S_T]", 3, kMartingale, 0, 0}}});
    g.push_back({"BS Asian (12 fixings)", request(Model::BlackScholes, Product::Asian, {C, N, N}, {100, 0, 0}, 12),
                 {{"R4", "BS arithmetic Asian call", 0, 5.575692, 0.000114, 0},
                  {"R3", "BS geometric Asian call", 1, 5.381250, 0, 0}}});
    g.push_back({"BS down-and-out (daily)", request(Model::BlackScholes, Product::Barrier, {C, N, N}, {100, 0, 0}, 252),
                 {{"R6", "BS DO call H 80 vs Python MC", 0, 9.245769, 0.007422, 0},
                  {"R5", "BS DO call H 80 vs BGK (indicative)", 0, 9.243397, 0, 0, true},
                  {"R1'", "BS call, 252-step paths", 1, 9.319738, 0, 0},
                  {"MG'", "BS E[S_T], 252 steps", 3, kMartingale, 0, 0}}});
    g.push_back({"Heston QE European", request(Model::Heston, Product::European, {C, C, P}, {100, 119, 80}, 252),
                 {{"R7a", "Heston call K 100 vs Fourier", 0, 8.403656, 0, 0.02},
                  {"R7b", "Heston call K 119 vs Fourier", 1, 1.010290, 0, 0.02},
                  {"R7c", "Heston put K 80 vs Fourier", 2, 1.737996, 0, 0.02},
                  {"MGh", "Heston E[S_T]", 3, kMartingale, 0, 0}}});
    g.push_back({"Heston QE down-and-out", request(Model::Heston, Product::Barrier, {C, N, N}, {100, 0, 0}, 252),
                 {{"R9", "Heston DO call H 80 vs QE (JS)", 0, 8.0927, 0.0092, 0},
                  {"R10", "Heston call K 100 vs QE (JS)", 1, 8.4153, 0.0131, 0},
                  {"MGh'", "Heston E[S_T], barrier paths", 3, kMartingale, 0, 0}}});
    for (Group& x : g) x.rq.n_paths = x.rq.model == Model::Heston ? n_heston : n_bs;
    return g;
}

bool report(const Check& c, const Estimate& e) {
    const double comb = std::sqrt(e.se * e.se + c.se_ref * c.se_ref);
    const double dev = e.price - c.ref, tol = 4 * comb + c.bias;
    const bool ok = std::fabs(dev) <= tol;
    std::printf("  %-5s %-38s %11.6f %9.6f %11.6f %+9.6f %+7.2f %9.6f  %s\n", c.id, c.name, e.price, e.se, c.ref, dev,
                comb > 0 ? dev / comb : 0.0, tol, c.info ? "info" : ok ? "PASS" : "FAIL");
    if (!ok && !c.info) ++g_failures;
    return ok;
}

void header() {
    std::printf("  %-5s %-38s %11s %9s %11s %9s %7s %9s\n", "id", "case", "price", "se", "ref", "diff", "n.se",
                "tol");
}

void run_suite(const char* title, long long n_bs, long long n_heston,
               const std::function<McResult(const McRequest&)>& price) {
    std::printf("\n== %s (BS %lld paths, Heston %lld paths x 252 steps)\n", title, n_bs, n_heston);
    header();
    for (const Group& g : groups(n_bs, n_heston)) {
        const McResult r = price(g.rq);
        std::printf(" %s: %.0f ms\n", g.name, r.total_ms);
        for (const Check& c : g.checks) report(c, r.out[c.output]);
    }
}

void closed_forms() {
    std::printf("\n== Closed forms (C++) against the reference table\n");
    struct {
        const char* id;
        double value, ref;
    } rows[] = {
        {"R1", bs_price(100, 100, 1, 0.04, 0.01, 0.2, true), 9.319738},
        {"R2", bs_price(100, 100, 1, 0.04, 0.01, 0.2, false), 6.393699},
        {"R3", geometric_asian_price(100, 100, 1, 0.04, 0.01, 0.2, 12, true), 5.381250},
        {"R5", down_out_call_bgk(100, 100, 80, 1, 0.04, 0.01, 0.2, 252), 9.243397},
    };
    for (const auto& r : rows) {
        const bool ok = std::fabs(r.value - r.ref) < 1e-6;
        std::printf("  %-5s %.9f vs %.6f  %s\n", r.id, r.value, r.ref, ok ? "PASS" : "FAIL");
        if (!ok) ++g_failures;
    }
}

// Float against double paths driven by the same normals: the gap is the float rounding error alone.
void float_vs_double(long long n_heston) {
    std::printf("\n== GPU float vs GPU double, same normals (Heston %lld paths x 252 steps)\n", n_heston);
    std::printf("  %-26s %-4s %12s %12s %12s %10s %9s\n", "case", "out", "float", "double", "gap", "gap/se", "");
    GpuConfig dbl;
    dbl.use_double = true;
    for (const Group& g : groups(2, n_heston)) {
        if (g.rq.model != Model::Heston) continue;
        const McResult f = gpu_price(g.rq), d = gpu_price(g.rq, dbl);
        for (int k : {0, 1, 3}) {
            const double gap = f.out[k].price - d.out[k].price, rel = gap / d.out[k].se;
            const bool ok = std::fabs(rel) < 0.5;  // float error well inside the Monte Carlo noise
            std::printf("  %-26s %-4d %12.6f %12.6f %+12.2e %+10.4f %9s\n", g.name, k, f.out[k].price,
                        d.out[k].price, gap, rel, ok ? "PASS" : "FAIL");
            if (!ok) ++g_failures;
        }
        std::printf("  (float kernel %.0f ms, double kernel %.0f ms)\n", f.kernel_ms, d.kernel_ms);
        std::printf("  double paths against the references:\n");
        header();
        for (const Check& c : g.checks) report(c, d.out[c.output]);
    }
}

void qe_bias() {
    const long long n = 100'000'000;
    std::printf("\n== Heston QE daily (252 steps) bias against Fourier, GPU float, %lld paths\n", n);
    McRequest rq = request(Model::Heston, Product::European, {Leg::Call, Leg::Call, Leg::Put}, {100, 119, 80}, 252);
    rq.n_paths = n;
    const McResult r = gpu_price(rq);
    const double refs[3] = {8.403656, 1.010290, 1.737996};
    const char* names[3] = {"call K 100", "call K 119", "put K 80"};
    for (int k = 0; k < 3; ++k)
        std::printf("  %-12s %.6f +- %.6f  Fourier %.6f  bias %+.6f (%+.1f se)\n", names[k], r.out[k].price,
                    r.out[k].se, refs[k], r.out[k].price - refs[k], (r.out[k].price - refs[k]) / r.out[k].se);
    std::printf("  E[S_T] %.6f +- %.6f vs %.6f (%+.1f se); kernel %.0f ms\n", r.out[3].price, r.out[3].se,
                kMartingale, (r.out[3].price - kMartingale) / r.out[3].se, r.kernel_ms);
}

}  // namespace

int main(int argc, char** argv) {
    const std::string mode = argc > 1 ? argv[1] : "all";
    const int threads = static_cast<int>(std::thread::hardware_concurrency());
    const bool all = mode == "all";
    if (all || mode == "closed") closed_forms();
    if (all || mode == "cpu")
        run_suite("CPU double reference", 1'000'000, 200'000, [&](const McRequest& rq) { return cpu_price(rq, threads); });
    if (all || mode == "gpu" || mode == "double") std::printf("\nGPU: %s\n", gpu_device_name().c_str());
    if (all || mode == "gpu")
        run_suite("GPU float", 10'000'000, 4'000'000, [](const McRequest& rq) { return gpu_price(rq); });
    if (all || mode == "double") float_vs_double(4'000'000);
    if (mode == "qe-bias") qe_bias();
    std::printf("\n%s: %d failure(s)\n", g_failures ? "FAILED" : "OK", g_failures);
    return g_failures ? 1 : 0;
}
