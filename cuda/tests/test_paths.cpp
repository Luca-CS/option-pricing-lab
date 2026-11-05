// Path engine checks that need no Python: forwards matched (martingale), the Black-Scholes call by its formula, the
// correlation of two names, and the GPU against the CPU path by path. Prints one line per check, exit code 1 on a
// failure. The full validation against the library is tests/test_native.py.
#include <cmath>
#include <cstdio>
#include <vector>

#include "optlab_cuda/capi.h"

namespace {

int failures = 0;

void expect(bool ok, const char* what, double got, double want, double tol) {
    std::printf("%-58s %s  got %.6f  want %.6f  tol %.6f\n", what, ok ? "PASS" : "FAIL", got, want, tol);
    failures += !ok;
}

struct Case {
    int n = 1, steps = 1;
    std::vector<double> dt, t0, root, cvar, hes, fwd;
    std::vector<int> obs, kind;
    long long pairs = 200000;
    std::vector<double> out;
    optlab_paths_request rq{};

    void build() {
        rq = {};
        rq.n_names = n, rq.n_steps = steps, rq.n_obs = static_cast<int>(obs.size()), rq.n_pairs = pairs;
        rq.seed = 20261105ull, rq.dt = dt.data(), rq.t0 = t0.data(), rq.obs_step = obs.data(), rq.root = root.data();
        rq.kind = kind.data(), rq.cvar = cvar.data(), rq.heston = hes.data(), rq.same = 1, rq.fwd = fwd.data();
        out.assign(static_cast<size_t>(n) * obs.size() * 2 * pairs, 0.0);
        rq.out = out.data();
    }
    const double* col(int a, int j) const { return out.data() + (static_cast<size_t>(a) * obs.size() + j) * 2 * pairs; }
};

double ncdf(double x) { return 0.5 * std::erfc(-x / std::sqrt(2.0)); }

void mean_se(const Case& c, int a, int j, double (*f)(double), double& m, double& se) {
    const double* x = c.col(a, j);
    double s = 0, s2 = 0;
    for (long long p = 0; p < c.pairs; ++p) {
        const double y = 0.5 * (f(x[p]) + f(x[c.pairs + p]));
        s += y, s2 += y * y;
    }
    m = s / c.pairs;
    se = std::sqrt((s2 / c.pairs - m * m) / (c.pairs - 1));
}

double ident(double x) { return x; }
double call100(double x) { return x > 100 ? x - 100 : 0; }

void run(Case& c, int backend, const char* label) {
    c.build();
    if (optlab_paths(&c.rq, backend, 0) != 0) {
        std::printf("%s: %s\n", label, optlab_last_error());
        ++failures;
    }
}

}  // namespace

int main() {
    // Black-Scholes, 20 %, one exact step to one year, forward 100
    Case bs;
    bs.dt = {1.0}, bs.t0 = {0.0}, bs.root = {1.0}, bs.cvar = {0.04}, bs.hes = {0, 1, 1, 0, 0}, bs.fwd = {100.0};
    bs.obs = {1}, bs.kind = {0};
    run(bs, 0, "cpu bs");
    double m, se;
    mean_se(bs, 0, 0, ident, m, se);
    expect(std::fabs(m - 100) < 4 * se, "CPU BS: E[S_T] = F", m, 100, 4 * se);
    mean_se(bs, 0, 0, call100, m, se);
    const double want = 100 * (ncdf(0.1) - ncdf(-0.1));
    expect(std::fabs(m - want) < 4 * se, "CPU BS: call K=F against Black's formula", m, want, 4 * se);

    // Heston, 2 years in 504 steps: the forward is matched whatever the discretisation
    Case h;
    h.steps = 504;
    for (int i = 0; i < h.steps; ++i) h.dt.push_back(2.0 / h.steps), h.t0.push_back(2.0 * i / h.steps);
    h.root = {1.0}, h.cvar = {0}, h.hes = {0.0228, 3.09, 0.0537, 1.31, -0.69}, h.fwd = {100.0, 100.0};
    h.obs = {252, 504}, h.kind = {2}, h.pairs = 100000;
    run(h, 0, "cpu heston");
    mean_se(h, 0, 1, ident, m, se);
    expect(std::fabs(m - 100) < 4 * se, "CPU Heston: E[S_T] = F at 2 years", m, 100, 4 * se);

    // Two names at correlation 0.6: correlation of the log returns
    Case two;
    two.n = 2, two.dt = {1.0}, two.t0 = {0.0}, two.root = {1.0, 0.0, 0.6, 0.8}, two.cvar = {0.04, 0.09};
    two.hes = std::vector<double>(10, 0.0), two.fwd = {100.0, 100.0}, two.obs = {1}, two.kind = {0, 0};
    for (int a = 0; a < 2; ++a) two.hes[a * 5 + 1] = two.hes[a * 5 + 2] = 1;
    run(two, 0, "cpu two names");
    {
        const double *a = two.col(0, 0), *b = two.col(1, 0);
        double sa = 0, sb = 0, saa = 0, sbb = 0, sab = 0;
        const long long n = 2 * two.pairs;
        for (long long i = 0; i < n; ++i) {
            const double x = std::log(a[i]), y = std::log(b[i]);
            sa += x, sb += y, saa += x * x, sbb += y * y, sab += x * y;
        }
        const double cov = sab / n - sa / n * sb / n, rho = cov / std::sqrt((saa / n - sa * sa / n / n) * (sbb / n - sb * sb / n / n));
        expect(std::fabs(rho - 0.6) < 0.005, "CPU two names: correlation of log returns", rho, 0.6, 0.005);
    }

    if (optlab_gpu_count() > 0) {
        Case g = h;
        run(g, 1, "gpu heston");
        // Same normals, so the paths agree up to float rounding. A few Heston paths do not: where the variance is near
        // zero, the rounding decides whether it is truncated, and the paths then part. Their share is checked.
        std::vector<double> rel(h.out.size());
        for (size_t i = 0; i < rel.size(); ++i) rel[i] = std::fabs(g.out[i] / h.out[i] - 1);
        size_t far = 0;
        double median_sum = 0;
        for (double r : rel) far += r > 1e-3, median_sum += r < 1e-3 ? r : 0;
        const double share = static_cast<double>(far) / rel.size();
        expect(share < 0.06, "GPU (float) against CPU (double), Heston: share > 1e-3", share, 0, 0.06);  // 3.7 % measured
        expect(median_sum / (rel.size() - far) < 1e-4, "GPU against CPU: mean relative gap of the others",
               median_sum / (rel.size() - far), 0, 1e-4);  // 2e-5 measured
        // what matters: the same price, the two estimates differing by less than 4 standard errors of their difference
        double sd = 0, sd2 = 0;
        const double* a = h.col(0, 1);
        const double* b = g.col(0, 1);
        for (long long p = 0; p < h.pairs; ++p) {
            const double d = 0.5 * (call100(a[p]) + call100(a[h.pairs + p])) - 0.5 * (call100(b[p]) + call100(b[h.pairs + p]));
            sd += d, sd2 += d * d;
        }
        const double md = sd / h.pairs, sed = std::sqrt((sd2 / h.pairs - md * md) / (h.pairs - 1));
        expect(std::fabs(md) < 4 * sed + 1e-12, "GPU against CPU: Heston call price, paired difference", md, 0, 4 * sed);
        mean_se(g, 0, 1, ident, m, se);
        expect(std::fabs(m - 100) < 4 * se, "GPU Heston: E[S_T] = F at 2 years", m, 100, 4 * se);
        std::printf("GPU Heston 2y x 504 steps, %lld paths: kernel %.2f ms, total %.2f ms\n", 2 * g.pairs,
                    g.rq.kernel_ms, g.rq.total_ms);
    } else {
        std::printf("no CUDA device: GPU checks skipped\n");
    }
    std::printf("%s\n", failures ? "FAILED" : "all passed");
    return failures ? 1 : 0;
}
