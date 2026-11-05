// Particle method for the SLV leverage: the host algorithm (bins to E[v | x] to the leverage row) and the CPU device.
#include "optlab_cuda/leverage.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <limits>
#include <thread>

#include "optlab_cuda/philox.hpp"

namespace optlab {

namespace {

double interp_uniform(double x, double a, double b, const double* y, int n) {
    double u = (x - a) / (b - a) * (n - 1);
    if (!(u > 0)) return y[0];
    if (u >= n - 1) return y[n - 1];
    const int i = static_cast<int>(u);
    const double w = u - i;
    return y[i] * (1 - w) + y[i + 1] * w;
}

double quantile(const Bins& b, double lo, double hi, double q) {
    long long total = 0;
    for (long long c : b.fine) total += c;
    const double target = q * static_cast<double>(total);
    const double width = (hi - lo) / static_cast<double>(b.fine.size());
    double cum = 0;
    for (size_t k = 0; k < b.fine.size(); ++k) {
        const double next = cum + static_cast<double>(b.fine[k]);
        if (next >= target && b.fine[k] > 0) return lo + (static_cast<double>(k) + (target - cum) / b.fine[k]) * width;
        cum = next;
    }
    return hi;
}

}  // namespace

double calibrate_leverage(const LeverageSpec& s, ParticleDevice& dev) {
    const auto t_start = std::chrono::steady_clock::now();
    const int nx = s.n_x, nb = s.n_bins;
    const double n_particles = 2.0 * static_cast<double>(s.n_pairs);
    std::vector<double> x_eval(nx), cond(nx), lev(nx), centres(nb);
    Bins bins;
    bins.fine.assign(s.n_fine, 0);
    bins.acc.assign(5 * static_cast<size_t>(nb), 0.0);
    double lo = 0, hi = 0;
    for (int i = 0; i < s.n_steps; ++i) {
        const double t0 = s.t0[i], h = s.dt[i];
        if (i == 0) {
            for (int g = 0; g < nx; ++g)
                x_eval[g] = (-6.0 + 12.0 * g / (nx - 1)) * s.atm_vol * std::sqrt(std::max(h, 1e-4)), cond[g] = s.v0;
        } else {
            std::fill(bins.fine.begin(), bins.fine.end(), 0);
            std::fill(bins.acc.begin(), bins.acc.end(), 0.0);
            bins.v_sum = 0.0;
            dev.bin(lo, hi, bins);
            const double a = quantile(bins, lo, hi, s.q_lo), b = quantile(bins, lo, hi, s.q_hi);
            for (int g = 0; g < nx; ++g) x_eval[g] = a + (b - a) * g / (nx - 1);
            const double bw = s.bandwidth_scale * s.atm_vol * std::sqrt(std::max(t0, 0.15)) * std::pow(n_particles, -0.2);
            const double we = (hi + 1e-12 - lo) / nb;
            for (int k = 0; k < nb; ++k) centres[k] = lo + (k + 0.5) * we;
            const double fallback = bins.v_sum / n_particles;
            const double *cnt = bins.acc.data(), *sv = cnt + nb, *sx = sv + nb, *sxx = sx + nb, *sxv = sxx + nb;
            for (int g = 0; g < nx; ++g) {
                double S0 = 0, T0 = 0, Ax = 0, Axx = 0, Axv = 0;
                const double x0 = x_eval[g];
                for (int k = 0; k < nb; ++k) {
                    const double d = (x0 - centres[k]) / bw, w = std::exp(-0.5 * d * d);
                    S0 += w * cnt[k], T0 += w * sv[k];
                    if (s.local_linear) Ax += w * sx[k], Axx += w * sxx[k], Axv += w * sxv[k];
                }
                const double nw = S0 > 1e-8 ? T0 / std::max(S0, 1e-300) : fallback;
                cond[g] = nw;
                if (s.local_linear) {
                    const double S1 = Ax - x0 * S0, S2 = Axx - 2 * x0 * Ax + x0 * x0 * S0, T1 = Axv - x0 * T0;
                    const double det = S0 * S2 - S1 * S1;
                    const bool ok = S0 >= 50 && det > 1e-12 * S0 * std::fabs(S2);
                    cond[g] = std::max(ok ? (S2 * T0 - S1 * T1) / det : nw, 1e-8);
                }
            }
        }
        const double* row = s.lv_rows + static_cast<long long>(i) * s.n_lv;
        const double lv_lo = s.lv_lo[i], lv_hi = lv_lo + (s.n_lv - 1) / s.lv_inv_dx[i];
        for (int g = 0; g < nx; ++g) {
            const double loc = interp_uniform(x_eval[g], lv_lo, lv_hi, row, s.n_lv);
            lev[g] = std::min(std::max(std::sqrt(loc / std::max(cond[g], 1e-8)), s.l_min), s.l_max);
            s.x_grids[static_cast<long long>(i) * nx + g] = x_eval[g];
            s.values[static_cast<long long>(i) * nx + g] = lev[g];
        }
        dev.step(i, h, x_eval[0], x_eval[nx - 1], lev, lo, hi);
    }
    return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t_start).count();
}

namespace {

class CpuParticles final : public ParticleDevice {
public:
    CpuParticles(const LeverageSpec& s, int threads) : s_(s), threads_(threads) {
        const size_t n = static_cast<size_t>(2 * s.n_pairs);
        x_.assign(n, 0.0);
        v_.assign(n, s.v0);
    }

    void bin(double lo, double hi, Bins& out) override {
        const int nf = static_cast<int>(out.fine.size()), nb = s_.n_bins;
        const double wf = (hi - lo) / nf, we = (hi + 1e-12 - lo) / nb;
        std::vector<Bins> part(threads_);
        for (auto& p : part) p.fine.assign(nf, 0), p.acc.assign(5 * static_cast<size_t>(nb), 0.0);
        parallel([&](int t, size_t a, size_t b) {
            Bins& p = part[t];
            for (size_t j = a; j < b; ++j) {
                const double x = x_[j], v = s_.positive_part ? std::max(v_[j], 0.0) : v_[j];
                int f = wf > 0 ? static_cast<int>((x - lo) / wf) : 0;
                p.fine[std::min(std::max(f, 0), nf - 1)] += 1;
                int k = static_cast<int>((x - lo) / we);
                k = std::min(std::max(k, 0), nb - 1);
                p.acc[k] += 1, p.acc[nb + k] += v, p.acc[2 * nb + k] += x, p.acc[3 * nb + k] += x * x;
                p.acc[4 * nb + k] += x * v;
                p.v_sum += v;
            }
        });
        for (const auto& p : part) {
            for (int k = 0; k < nf; ++k) out.fine[k] += p.fine[k];
            for (size_t k = 0; k < out.acc.size(); ++k) out.acc[k] += p.acc[k];
            out.v_sum += p.v_sum;
        }
    }

    void step(int i, double h, double a, double b, const std::vector<double>& lev, double& lo, double& hi) override {
        const long long np = s_.n_pairs;
        const double rc = std::sqrt(1 - s_.rho * s_.rho);
        const std::uint32_t k0 = static_cast<std::uint32_t>(s_.seed), k1 = static_cast<std::uint32_t>(s_.seed >> 32);
        std::vector<double> los(threads_, std::numeric_limits<double>::infinity()), his(threads_, -std::numeric_limits<double>::infinity());
        parallel_pairs([&](int t, long long pa, long long pb) {
            double mn = los[t], mx = his[t];
            for (long long p = pa; p < pb; ++p) {
                double z[4];
                normals4<double>(static_cast<std::uint64_t>(p), static_cast<std::uint32_t>(i + 1), 0u, k0, k1, z);
                for (int leg = 0; leg < 2; ++leg) {
                    const size_t j = static_cast<size_t>(leg ? np + p : p);
                    const double sg = leg ? -1.0 : 1.0, z1 = sg * z[0], z2 = sg * z[1];
                    const double L = interp_uniform(x_[j], a, b, lev.data(), s_.n_x);
                    const double vp = std::max(v_[j], 0.0), var = L * L * vp;
                    const double zx = s_.rho * z2 + rc * z1;
                    v_[j] += s_.kappa * (s_.theta - vp) * h + s_.xi * std::sqrt(vp * h) * z2;
                    x_[j] += -0.5 * var * h + std::sqrt(var * h) * zx;
                    mn = std::min(mn, x_[j]), mx = std::max(mx, x_[j]);
                }
            }
            los[t] = mn, his[t] = mx;
        });
        lo = *std::min_element(los.begin(), los.end());
        hi = *std::max_element(his.begin(), his.end());
    }

private:
    template <typename F>
    void parallel(F f) {  // over particles
        const size_t n = x_.size(), per = (n + threads_ - 1) / threads_;
        std::vector<std::thread> pool;
        for (int t = 0; t < threads_; ++t) {
            const size_t a = t * per, b = std::min(n, a + per);
            if (a < b) pool.emplace_back(f, t, a, b);
        }
        for (auto& th : pool) th.join();
    }
    template <typename F>
    void parallel_pairs(F f) {
        const long long n = s_.n_pairs, per = (n + threads_ - 1) / threads_;
        std::vector<std::thread> pool;
        for (int t = 0; t < threads_; ++t) {
            const long long a = t * per, b = std::min(n, a + per);
            if (a < b) pool.emplace_back(f, t, a, b);
        }
        for (auto& th : pool) th.join();
    }

    const LeverageSpec& s_;
    int threads_;
    std::vector<double> x_, v_;
};

}  // namespace

double cpu_leverage(const LeverageSpec& s, int n_threads) {
    const int hw = static_cast<int>(std::thread::hardware_concurrency());
    CpuParticles dev(s, n_threads > 0 ? n_threads : (hw > 0 ? hw : 1));
    return calibrate_leverage(s, dev);
}

}  // namespace optlab
