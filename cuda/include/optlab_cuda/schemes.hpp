// Path schemes shared by the CPU reference (double) and the GPU kernels (float or double), so that both run the
// same algorithm by construction. Every path is simulated in the log-return y = ln(S_t / S0), which starts at 0:
// in float this keeps about three more significant bits than x = ln S_t (about 4.6 at S0 = 100).
//
// Opt is a traits class with two compile-time switches used here:
//   Opt::fast_math  hardware approximations __expf / __logf on the GPU (float only)
//   Opt::legs       2 for antithetic pairs (Z, -Z), 1 for plain paths
#pragma once

#include <cmath>

#include "params.hpp"

#ifdef __CUDACC__
#define OPTLAB_HD __host__ __device__ __forceinline__
#else
#define OPTLAB_HD inline
#endif

namespace optlab {

struct SchemeOpt {
    static constexpr bool fast_math = false;
    static constexpr int legs = 2;
};

// Math functions dispatched on the real type, so that a template never promotes float to double by accident
// (FP64 runs at 1/32 of FP32 on Turing).
template <bool Fast = false>
OPTLAB_HD float m_exp(float x) {
#ifdef __CUDA_ARCH__
    if constexpr (Fast) return __expf(x);
    return expf(x);
#else
    return std::exp(x);
#endif
}
template <bool Fast = false>
OPTLAB_HD float m_log(float x) {
#ifdef __CUDA_ARCH__
    if constexpr (Fast) return __logf(x);
    return logf(x);
#else
    return std::log(x);
#endif
}
OPTLAB_HD float m_sqrt(float x) {
#ifdef __CUDA_ARCH__
    return sqrtf(x);
#else
    return std::sqrt(x);
#endif
}
OPTLAB_HD float m_ncdf(float x) {
#ifdef __CUDA_ARCH__
    return normcdff(x);
#else
    return 0.5f * std::erfc(-x * 0.70710678f);
#endif
}
template <bool Fast = false>
OPTLAB_HD double m_exp(double x) {
#ifdef __CUDA_ARCH__
    return ::exp(x);
#else
    return std::exp(x);
#endif
}
template <bool Fast = false>
OPTLAB_HD double m_log(double x) {
#ifdef __CUDA_ARCH__
    return ::log(x);
#else
    return std::log(x);
#endif
}
OPTLAB_HD double m_sqrt(double x) {
#ifdef __CUDA_ARCH__
    return ::sqrt(x);
#else
    return std::sqrt(x);
#endif
}
OPTLAB_HD double m_ncdf(double x) {
#ifdef __CUDA_ARCH__
    return normcdf(x);
#else
    return 0.5 * std::erfc(-x * 0.70710678118654752);
#endif
}
template <typename R> OPTLAB_HD R m_max(R a, R b) { return a > b ? a : b; }

// Everything a path needs, precomputed on the host in double and cast once to R.
template <typename R>
struct PathConsts {
    R S0, D;                // spot, discount factor exp(-r T)
    R bs_drift, bs_vol;     // Black-Scholes step: (r - q - sigma^2 / 2) dt, sigma sqrt(dt)
    R ln_h;                 // ln(H / S0)
    R K[3];
    int leg[3];             // Leg as int
    int n_steps;
    R inv_n;                // 1 / n_steps (Asian average)
    // Heston, Andersen's QE-M scheme (2008), constants of one step dt
    R v0, theta, mu_dt, E, c1, c2, K1, K2, K3, K4, A, K0p;
};

template <typename R>
PathConsts<R> make_consts(const McRequest& rq) {
    const MarketParams& m = rq.market;
    const ProductParams& p = rq.product;
    const double dt = m.T / p.n_steps;
    PathConsts<R> c{};
    c.S0 = R(m.S0);
    c.D = R(std::exp(-m.r * m.T));
    const double s = rq.bs.sigma;
    c.bs_drift = R((m.r - m.q - 0.5 * s * s) * dt);
    c.bs_vol = R(s * std::sqrt(dt));
    c.ln_h = R(std::log(p.H / m.S0));
    for (int k = 0; k < 3; ++k) {
        c.K[k] = R(p.strikes[k]);
        c.leg[k] = static_cast<int>(p.legs[k]);
    }
    c.n_steps = p.n_steps;
    c.inv_n = R(1.0 / p.n_steps);
    const HestonParams& h = rq.heston;
    const double E = std::exp(-h.kappa * dt), xi2 = h.xi * h.xi, rho = h.rho;
    const double k12 = 0.5 * dt * (h.kappa * rho / h.xi - 0.5);
    c.v0 = R(h.v0);
    c.theta = R(h.theta);
    c.mu_dt = R((m.r - m.q) * dt);
    c.E = R(E);
    c.c1 = R(xi2 * E * (1 - E) / h.kappa);
    c.c2 = R(h.theta * xi2 * (1 - E) * (1 - E) / (2 * h.kappa));
    c.K1 = R(k12 - rho / h.xi);
    c.K2 = R(k12 + rho / h.xi);
    c.K3 = R(0.5 * dt * (1 - rho * rho));
    c.K4 = c.K3;
    c.A = R((k12 + rho / h.xi) + 0.25 * dt * (1 - rho * rho));
    c.K0p = R(-rho * h.kappa * h.theta * dt / h.xi);
    return c;
}

template <typename R>
OPTLAB_HD R leg_payoff(int leg, R S, R K) {
    switch (leg) {
        case static_cast<int>(Leg::Call): return m_max(S - K, R(0));
        case static_cast<int>(Leg::Put): return m_max(K - S, R(0));
        case static_cast<int>(Leg::Forward): return S - K;
        default: return R(0);
    }
}

// One QE-M step of (y, v) with independent normals zv (variance) and zx (log-spot).
template <typename R, bool Fast = false>
OPTLAB_HD void qe_step(const PathConsts<R>& c, R& y, R& v, R zv, R zx) {
    const R m = c.theta + (v - c.theta) * c.E;
    const R psi = (v * c.c1 + c.c2) / (m * m);
    R vn, K0;
    if (psi <= R(1.5)) {  // quadratic branch: v' = a (b + Zv)^2
        const R inv = R(2) / psi;
        const R b2 = inv - R(1) + m_sqrt(inv) * m_sqrt(m_max(inv - R(1), R(0)));
        const R a = m / (R(1) + b2), b = m_sqrt(b2);
        vn = a * (b + zv) * (b + zv);
        K0 = c.A < R(1) / (R(2) * a)
                 ? -c.A * b2 * a / (R(1) - R(2) * c.A * a) + R(0.5) * m_log<Fast>(R(1) - R(2) * c.A * a) -
                       (c.K1 + R(0.5) * c.K3) * v
                 : c.K0p;
    } else {  // exponential branch: mass p at zero, exponential tail
        const R p = (psi - R(1)) / (psi + R(1)), beta = (R(1) - p) / m;
        // 1 - U computed as Phi(-Zv), exact in both tails (no 1 - U cancellation; |Z| <= 6.7 with Box-Muller)
        const R U = m_ncdf(zv), one_minus_u = m_ncdf(-zv);
        vn = U <= p ? R(0) : m_log<Fast>((R(1) - p) / one_minus_u) / beta;
        K0 = c.A < beta ? -m_log<Fast>(p + beta * (R(1) - p) / (beta - c.A)) - (c.K1 + R(0.5) * c.K3) * v : c.K0p;
    }
    y += c.mu_dt + K0 + c.K1 * v + c.K2 * vn + m_sqrt(m_max(c.K3 * v + c.K4 * vn, R(0))) * zx;
    v = vn;
}

// European under Black-Scholes, one exact step: the outputs of one unit (pair, or single path) for one normal z.
template <typename R, typename Opt = SchemeOpt>
OPTLAB_HD void bs_european_unit(const PathConsts<R>& c, R z, R out[kOutputs]) {
    R o[kOutputs] = {R(0), R(0), R(0), R(0)};
    for (int a = 0; a < Opt::legs; ++a) {
        const R S = c.S0 * m_exp<Opt::fast_math>(c.bs_drift + c.bs_vol * (a == 0 ? z : -z));
        for (int k = 0; k < 3; ++k) o[k] += c.D * leg_payoff(c.leg[k], S, c.K[k]);
        o[3] += S;
    }
    for (int k = 0; k < kOutputs; ++k) out[k] = Opt::legs == 2 ? R(0.5) * o[k] : o[k];
}

// One unit (antithetic pair, or single path) on n_steps steps. Src::next4(R z[4]) supplies normals four at a
// time: Black-Scholes uses one per step, Heston two (zv, zx). Normals left over at the end of a path are dropped.
template <typename R, Model M, Product P, typename Opt = SchemeOpt, typename Src>
OPTLAB_HD void path_unit(const PathConsts<R>& c, Src& src, R out[kOutputs]) {
    constexpr int L = Opt::legs;
    constexpr bool F = Opt::fast_math;
    R y[L];
    [[maybe_unused]] R v[L], sum_s[L], sum_y[L];  // used or not depending on (M, P)
    [[maybe_unused]] bool alive[L];
    for (int a = 0; a < L; ++a) y[a] = R(0), v[a] = c.v0, sum_s[a] = R(0), sum_y[a] = R(0), alive[a] = true;
    R z[4];
    constexpr int per_step = M == Model::Heston ? 2 : 1;
    for (int i = 0; i < c.n_steps;) {
        src.next4(z);
        for (int j = 0; j + per_step <= 4 && i < c.n_steps; j += per_step, ++i) {
            for (int a = 0; a < L; ++a) {
                const R sg = a == 0 ? R(1) : R(-1);
                if constexpr (M == Model::Heston)
                    qe_step<R, F>(c, y[a], v[a], sg * z[j], sg * z[j + 1]);
                else
                    y[a] += c.bs_drift + c.bs_vol * sg * z[j];
                if constexpr (P == Product::Asian) {
                    sum_s[a] += m_exp<F>(y[a]);
                    sum_y[a] += y[a];
                }
                if constexpr (P == Product::Barrier) alive[a] = alive[a] && !(y[a] <= c.ln_h);
            }
        }
    }
    R o[kOutputs] = {R(0), R(0), R(0), R(0)};
    for (int a = 0; a < L; ++a) {
        const R S = c.S0 * m_exp<F>(y[a]);
        if constexpr (P == Product::European) {
            for (int k = 0; k < 3; ++k) o[k] += c.D * leg_payoff(c.leg[k], S, c.K[k]);
        } else if constexpr (P == Product::Asian) {
            o[0] += c.D * leg_payoff(c.leg[0], c.S0 * sum_s[a] * c.inv_n, c.K[0]);
            o[1] += c.D * leg_payoff(c.leg[0], c.S0 * m_exp<F>(sum_y[a] * c.inv_n), c.K[0]);
        } else {
            const R pay = c.D * leg_payoff(c.leg[0], S, c.K[0]);
            o[0] += alive[a] ? pay : R(0);
            o[1] += pay;
        }
        o[3] += S;
    }
    for (int k = 0; k < kOutputs; ++k) out[k] = L == 2 ? R(0.5) * o[k] : o[k];
}

}  // namespace optlab
