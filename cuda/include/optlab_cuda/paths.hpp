// Path generation for the Python library: the discretisation of `optlab.models.simulate` and of
// `optlab.structured.multiasset.simulate_basket`, for every model of the library, on the CPU (double) and the GPU
// (float), by the same code.
//
// Each name is simulated in its log-forward x = ln(S_t / F(t)) with a log-Euler step on the variance at the start of
// the step, and full truncation for the variance process:
//     var = c                     constant (Black-Scholes)
//         = T(t, x)               a table: Dupire local variance
//         = max(v, 0)             Heston
//         = T(t, x) max(v, 0)     SLV, the table holding the squared leverage
//     v  += kappa (theta - v+) dt + xi sqrt(v+ dt) (rho z + sqrt(1 - rho^2) w)
//     x  += -var dt / 2 + sqrt(var dt) z
// Tables are rows on uniform grids of x, one row per time step, read by linear interpolation and flat beyond (as
// `optlab.structured.fastmodels`). The spot normals z are correlated by a matrix root of the correlation, the
// variance residuals w by the same root or independently, and an optional local correlation mixes the spots with a
// common factor by a weight that depends on the level of the basket (`optlab.structured.localcorr`). The variance is
// always driven by the spot draws (rho z + ...), so local vol and SLV share their spot normals on a seed.
//
// Draws come from philox.hpp, a function of (seed, global pair index, step, stream): pair p gives rows p (z) and
// n_pairs + p (-z) of the output. Prices therefore depend on (seed, number of pairs) only.
#pragma once

#include <cmath>
#include <cstdint>

#include "philox.hpp"

#ifdef __CUDACC__
#define OPTLAB_PD __host__ __device__ __forceinline__
#else
#define OPTLAB_PD inline
#endif

namespace optlab {

constexpr int kMaxNames = 8;
constexpr int kMaxWindows = 2;

enum VarKind : int { kConstVar = 0, kTableVar = 1, kHestonVar = 2, kSlvVar = 3 };

// Everything a path needs, as pointers into one memory space (host for the CPU, device for the GPU) of the real
// type R. The output is double, n_cols columns per name: out[(a * n_cols + c) * rows + row]. Observation j goes to
// column col_of[j] (or nowhere when -1), then each window w (observations win_lo[w] to win_hi[w], at most
// kMaxWindows) gives its lowest and highest spot in columns n_keep + 2 w and n_keep + 2 w + 1: a daily barrier needs
// two columns instead of one per day.
template <typename R>
struct PathSpec {
    int n_names = 1, n_steps = 0, n_obs = 0, n_x = 0;
    long long n_pairs = 0;     // pairs in this call
    long long first_pair = 0;  // global index of the first one
    long long rows = 0;        // rows of the output (2 n_pairs), and its column stride
    long long row0 = 0;        // first row written: chunks of one call write into one output
    long long out_pairs = 0;   // pairs of the whole output: leg 1 of pair p goes to row out_pairs + p
    std::uint32_t key0 = 0, key1 = 0;
    const R* dt = nullptr;         // n_steps
    const R* t0 = nullptr;         // n_steps, start time of each step
    const int* obs_step = nullptr;  // n_obs, nondecreasing: observation j is taken after step obs_step[j] (0: at t = 0)
    const R* root = nullptr;       // n_names x n_names, row major: z = root raw
    const int* kind = nullptr;     // n_names
    const R* cvar = nullptr;       // n_names, constant variance
    const R* hes = nullptr;        // n_names x 5: v0, kappa, theta, xi, rho
    int same = 1;                  // variance residuals correlated like the spots
    const int* step_row = nullptr;  // n_names x n_steps: table row of each step (absolute index)
    const R* lo = nullptr;         // per table row
    const R* inv_dx = nullptr;     // per table row
    const R* table = nullptr;      // rows x n_x
    const R* fwd = nullptr;        // n_names x n_obs: F(t_j)
    int mixer = 0;                 // 1: local correlation
    R mix[8] = {};                 // w0, level, down, up, sigma, market sign, cos(turn), sin(turn)
    const R* low_root = nullptr;   // n_names x n_names
    int n_cols = 0, n_keep = 0;    // columns per name; the first n_keep hold observations
    const int* col_of = nullptr;   // n_obs
    int n_win = 0;
    const int* win_lo = nullptr;   // n_win, first observation of each window
    const int* win_hi = nullptr;   // n_win, last one
    double* out = nullptr;
};

template <typename R>
OPTLAB_PD R p_sqrt(R x) {
#ifdef __CUDA_ARCH__
    if constexpr (sizeof(R) == 4) return sqrtf(x); else return sqrt(x);
#else
    return std::sqrt(x);
#endif
}
template <typename R>
OPTLAB_PD R p_exp(R x) {
#ifdef __CUDA_ARCH__
    if constexpr (sizeof(R) == 4) return expf(x); else return exp(x);
#else
    return std::exp(x);
#endif
}
template <typename R>
OPTLAB_PD R p_log(R x) {
#ifdef __CUDA_ARCH__
    if constexpr (sizeof(R) == 4) return logf(x); else return log(x);
#else
    return std::log(x);
#endif
}
template <typename R>
OPTLAB_PD R p_max(R a, R b) { return a > b ? a : b; }
template <typename R>
OPTLAB_PD R p_min(R a, R b) { return a < b ? a : b; }

template <typename R>
OPTLAB_PD R table_value(const PathSpec<R>& s, int row, R x) {
    R u = (x - s.lo[row]) * s.inv_dx[row];
    const R top = static_cast<R>(s.n_x - 1) - static_cast<R>(1e-4);
    u = p_min(p_max(u, R(0)), top);
    const int i = static_cast<int>(u);
    const R w = u - static_cast<R>(i);
    const R* y = s.table + static_cast<long long>(row) * s.n_x;
    return y[i] * (R(1) - w) + y[i + 1] * w;
}

// Normals of one stream of one step, n of them (n <= kMaxNames), four per Philox call.
template <typename R, int N>
OPTLAB_PD void draw(std::uint64_t pair, int step, int stream, std::uint32_t k0, std::uint32_t k1, int n, R* z) {
    R buf[4];
    for (int b = 0; b * 4 < n; ++b) {
        normals4<R>(pair, static_cast<std::uint32_t>(step), static_cast<std::uint32_t>(stream * 16 + b), k0, k1, buf);
        for (int q = 0; q < 4 && b * 4 + q < n; ++q) z[b * 4 + q] = buf[q];
    }
}

// Running extremes of the windows, per leg, name and window.
template <typename R, int N>
struct Extremes {
    R lo[2][N][kMaxWindows], hi[2][N][kMaxWindows];
};

template <typename R, int N>
OPTLAB_PD void record(const PathSpec<R>& s, long long p, int j, const R* x0, const R* x1, Extremes<R, N>& e) {
    const long long r[2] = {s.row0 + p, s.out_pairs + s.row0 + p};
    const int c = s.col_of[j];
    for (int a = 0; a < s.n_names; ++a) {
        const R f = s.fwd[a * s.n_obs + j];
        const R S[2] = {f * p_exp(x0[a]), f * p_exp(x1[a])};
        double* base = s.out + static_cast<long long>(a) * s.n_cols * s.rows;
        if (c >= 0) {
            base[c * s.rows + r[0]] = static_cast<double>(S[0]);
            base[c * s.rows + r[1]] = static_cast<double>(S[1]);
        }
        for (int w = 0; w < s.n_win; ++w) {
            if (j < s.win_lo[w] || j > s.win_hi[w]) continue;
            for (int leg = 0; leg < 2; ++leg) {
                if (j == s.win_lo[w]) {
                    e.lo[leg][a][w] = e.hi[leg][a][w] = S[leg];
                } else {
                    e.lo[leg][a][w] = p_min(e.lo[leg][a][w], S[leg]);
                    e.hi[leg][a][w] = p_max(e.hi[leg][a][w], S[leg]);
                }
            }
            if (j == s.win_hi[w]) {
                const long long cl = s.n_keep + 2 * w, ch = cl + 1;
                for (int leg = 0; leg < 2; ++leg) {
                    base[cl * s.rows + r[leg]] = static_cast<double>(e.lo[leg][a][w]);
                    base[ch * s.rows + r[leg]] = static_cast<double>(e.hi[leg][a][w]);
                }
            }
        }
    }
}

// Both legs of pair p (local index in this call) through every step. N is the compile-time bound on the names.
template <typename R, int N>
OPTLAB_PD void simulate_pair(const PathSpec<R>& s, long long p) {
    const int n = s.n_names;
    const std::uint64_t pair = static_cast<std::uint64_t>(s.first_pair + p);
    R x[2][N], v[2][N];
    bool has_var = false;
    for (int a = 0; a < n; ++a) {
        const int k = s.kind[a];
        const bool hv = k == kHestonVar || k == kSlvVar;
        has_var |= hv;
        x[0][a] = x[1][a] = R(0);
        v[0][a] = v[1][a] = hv ? s.hes[a * 5] : R(0);
    }
    Extremes<R, N> ext;
    int j = 0;
    while (j < s.n_obs && s.obs_step[j] == 0) record<R, N>(s, p, j++, x[0], x[1], ext);
    R zs[N], zr[N], fresh[4];
    for (int i = 1; i <= s.n_steps; ++i) {
        const R dt = s.dt[i - 1], t0 = s.t0[i - 1];
        draw<R, N>(pair, i, 0, s.key0, s.key1, n, zs);
        if (has_var) draw<R, N>(pair, i, 1, s.key0, s.key1, n, zr);
        if (s.mixer) draw<R, 4>(pair, i, 2, s.key0, s.key1, 1, fresh);
        for (int leg = 0; leg < 2; ++leg) {
            const R sign = leg ? R(-1) : R(1);
            R z[N], w[N];
            if (s.mixer) {
                // y = ln of the mean of S_i / F_i at the start of the step
                R m = R(0);
                for (int a = 0; a < n; ++a) m += p_exp(x[leg][a]);
                const R y = p_log(m / static_cast<R>(n));
                const R u = y / (s.mix[4] * p_sqrt(p_max(t0, R(1.0 / 12))));
                R wt = s.mix[0] + s.mix[1] + s.mix[2] * p_max(-u, R(0)) - s.mix[3] * p_max(u, R(0));
                wt = p_min(p_max(wt, R(0)), R(1));
                const R market = s.mix[5] * sign * zs[n - 1], fr = sign * fresh[0];
                const R common = s.mix[6] * market + s.mix[7] * fr;
                R other[N];
                for (int a = 0; a < n; ++a) other[a] = sign * zs[a];
                other[n - 1] = s.mix[7] * market - s.mix[6] * fr;
                const R sw = p_sqrt(wt), sl = p_sqrt(R(1) - wt);
                for (int a = 0; a < n; ++a) {
                    R acc = R(0);
                    for (int b = 0; b < n; ++b) acc += s.low_root[a * n + b] * other[b];
                    z[a] = sw * common + sl * acc;
                }
            } else {
                for (int a = 0; a < n; ++a) {
                    R acc = R(0);
                    for (int b = 0; b < n; ++b) acc += s.root[a * n + b] * zs[b];
                    z[a] = sign * acc;
                }
            }
            if (has_var) {
                for (int a = 0; a < n; ++a) {
                    if (s.same) {
                        R acc = R(0);
                        for (int b = 0; b < n; ++b) acc += s.root[a * n + b] * zr[b];
                        w[a] = sign * acc;
                    } else {
                        w[a] = sign * zr[a];
                    }
                }
            }
            for (int a = 0; a < n; ++a) {
                const int k = s.kind[a];
                R var;
                const R vp = p_max(v[leg][a], R(0));
                if (k == kConstVar) var = s.cvar[a];
                else if (k == kHestonVar) var = vp;
                else {
                    const R t = table_value(s, s.step_row[a * s.n_steps + (i - 1)], x[leg][a]);
                    var = k == kTableVar ? t : t * vp;
                }
                if (k == kHestonVar || k == kSlvVar) {
                    const R* h = s.hes + a * 5;
                    const R zv = h[4] * z[a] + p_sqrt(R(1) - h[4] * h[4]) * w[a];
                    v[leg][a] += h[1] * (h[2] - vp) * dt + h[3] * p_sqrt(vp * dt) * zv;
                }
                x[leg][a] += R(-0.5) * var * dt + p_sqrt(var * dt) * z[a];
            }
        }
        while (j < s.n_obs && s.obs_step[j] == i) record<R, N>(s, p, j++, x[0], x[1], ext);
    }
}

}  // namespace optlab
