// Requests (include/optlab_cuda/requests.hpp): checks a request, converts it and runs it; errors are exceptions.
// Without CUDA (OPTLAB_HAVE_CUDA undefined) the library still holds the CPU engines and reports no device.
#include "optlab_cuda/requests.hpp"

#include <cmath>
#include <cstdio>
#include <cstring>
#include <exception>
#include <stdexcept>
#include <string>
#include <thread>

#include "optlab_cuda/cpu_mc.hpp"
#include "optlab_cuda/leverage.hpp"
#include "optlab_cuda/paths_engine.hpp"
#ifdef OPTLAB_HAVE_CUDA
#include "optlab_cuda/gpu_mc.cuh"
#endif

namespace {

int device_count() {
#ifdef OPTLAB_HAVE_CUDA
    return optlab::gpu_count();
#else
    return 0;
#endif
}

optlab::McRequest convert(const optlab_request& c) {
    if (c.model < 0 || c.model > 1) throw std::invalid_argument("model must be 0 (Black-Scholes) or 1 (Heston)");
    if (c.product < 0 || c.product > 2) throw std::invalid_argument("product must be 0, 1 or 2");
    if (c.n_steps < 1) throw std::invalid_argument("n_steps must be >= 1");
    if (c.n_paths < 4 || c.n_paths % 2) throw std::invalid_argument("n_paths must be even and >= 4");
    if (!(c.S0 > 0 && c.T > 0)) throw std::invalid_argument("S0 and T must be positive");
    if (c.model == 0 && !(c.sigma > 0)) throw std::invalid_argument("sigma must be positive");
    if (c.model == 1 && !(c.v0 >= 0 && c.kappa > 0 && c.theta > 0 && c.xi > 0 && std::fabs(c.rho) <= 1))
        throw std::invalid_argument("Heston parameters out of range");
    if (c.product == 2 && !(c.H > 0 && c.H < c.S0)) throw std::invalid_argument("barrier: need 0 < H < S0");
    optlab::McRequest rq;
    rq.model = static_cast<optlab::Model>(c.model);
    rq.product.product = static_cast<optlab::Product>(c.product);
    for (int k = 0; k < 3; ++k) {
        if (c.legs[k] < 0 || c.legs[k] > 3) throw std::invalid_argument("legs must be 0 to 3");
        rq.product.legs[k] = static_cast<optlab::Leg>(c.legs[k]);
        rq.product.strikes[k] = c.strikes[k];
    }
    rq.product.H = c.H;
    rq.product.n_steps = c.n_steps;
    rq.market = {c.S0, c.r, c.q, c.T};
    rq.bs.sigma = c.sigma;
    rq.heston = {c.v0, c.kappa, c.theta, c.xi, c.rho};
    rq.n_paths = c.n_paths;
    rq.seed = c.seed;
    return rq;
}

optlab::PathSpec<double> path_spec(const optlab_paths_request& c) {
    if (c.n_names < 1 || c.n_names > optlab::kMaxNames) throw std::invalid_argument("n_names must be 1 to 8");
    if (c.n_pairs < 1 || c.first_pair < 0) throw std::invalid_argument("n_pairs must be >= 1 and first_pair >= 0");
    if (c.n_steps < 0 || c.n_obs < 0) throw std::invalid_argument("n_steps and n_obs must be >= 0");
    if (!c.out && c.n_obs > 0) throw std::invalid_argument("null output");
    if (c.n_steps > 0 && (!c.dt || !c.t0)) throw std::invalid_argument("null time grid");
    if (!c.root || !c.kind || !c.cvar || !c.heston || !c.fwd || (c.n_obs && !c.obs_step))
        throw std::invalid_argument("null array");
    bool table = false;
    for (int a = 0; a < c.n_names; ++a) {
        const int k = c.kind[a];
        if (k < 0 || k > 3) throw std::invalid_argument("kind must be 0 to 3");
        table |= k == 1 || k == 3;
        if ((k == 2 || k == 3) && !(c.heston[a * 5 + 1] > 0 && c.heston[a * 5 + 2] > 0 && c.heston[a * 5 + 3] >= 0 &&
                                   std::fabs(c.heston[a * 5 + 4]) <= 1))
            throw std::invalid_argument("Heston parameters out of range");
    }
    if (table) {
        if (c.n_x < 2 || c.n_rows < 1 || !c.step_row || !c.row_lo || !c.row_inv_dx || !c.table)
            throw std::invalid_argument("a tabulated variance needs n_x >= 2, rows and step_row");
        for (long long i = 0; i < static_cast<long long>(c.n_names) * c.n_steps; ++i) {
            const int k = c.kind[i / (c.n_steps ? c.n_steps : 1)];
            if ((k == 1 || k == 3) && (c.step_row[i] < 0 || c.step_row[i] >= c.n_rows))
                throw std::invalid_argument("step_row out of range");
        }
    }
    int prev = 0;
    for (int j = 0; j < c.n_obs; ++j) {
        if (c.obs_step[j] < prev || c.obs_step[j] > c.n_steps) throw std::invalid_argument("obs_step must be nondecreasing in [0, n_steps]");
        prev = c.obs_step[j];
    }
    if (c.mixer && (c.mixer != 1 || !c.low_root || !(c.mix[4] > 0))) throw std::invalid_argument("bad local correlation");
    if (c.n_windows < 0 || c.n_windows > optlab::kMaxWindows || (c.n_windows && (!c.win_lo || !c.win_hi)))
        throw std::invalid_argument("n_windows must be 0 to 2, with win_lo and win_hi");
    if (c.n_obs && !c.col_of) throw std::invalid_argument("null col_of");
    if (c.n_keep < 0 || c.n_cols != c.n_keep + 2 * c.n_windows) throw std::invalid_argument("n_cols must be n_keep + 2 n_windows");
    for (int j = 0; j < c.n_obs; ++j)
        if (c.col_of[j] < -1 || c.col_of[j] >= c.n_keep) throw std::invalid_argument("col_of out of range");
    for (int w = 0; w < c.n_windows; ++w)
        if (c.win_lo[w] < 0 || c.win_hi[w] < c.win_lo[w] || c.win_hi[w] >= c.n_obs) throw std::invalid_argument("bad window");
    optlab::PathSpec<double> s;
    s.n_names = c.n_names, s.n_steps = c.n_steps, s.n_obs = c.n_obs, s.n_x = c.n_x;
    s.n_pairs = c.n_pairs, s.first_pair = c.first_pair, s.rows = 2 * c.n_pairs, s.row0 = 0, s.out_pairs = c.n_pairs;
    s.key0 = static_cast<std::uint32_t>(c.seed), s.key1 = static_cast<std::uint32_t>(c.seed >> 32);
    s.dt = c.dt, s.t0 = c.t0, s.obs_step = c.obs_step, s.root = c.root, s.kind = c.kind, s.cvar = c.cvar;
    s.hes = c.heston, s.same = c.same, s.step_row = c.step_row, s.lo = c.row_lo, s.inv_dx = c.row_inv_dx;
    s.table = c.table, s.fwd = c.fwd, s.mixer = c.mixer, s.low_root = c.low_root, s.out = c.out;
    s.n_cols = c.n_cols, s.n_keep = c.n_keep, s.col_of = c.col_of, s.n_win = c.n_windows, s.win_lo = c.win_lo;
    s.win_hi = c.win_hi;
    for (int k = 0; k < 8; ++k) s.mix[k] = c.mix[k];
    return s;
}

}  // namespace

namespace optlab::api {

int gpu_count() { return device_count(); }

bool has_cuda() {
#ifdef OPTLAB_HAVE_CUDA
    return true;
#else
    return false;
#endif
}

int cpu_threads() { return static_cast<int>(std::thread::hardware_concurrency()); }

optlab_result price(const optlab_request& rq, int backend, int n_threads) {
    const optlab::McRequest r = convert(rq);
    optlab::McResult res;
    if (backend == 0) {
        const int th = n_threads > 0 ? n_threads : static_cast<int>(std::thread::hardware_concurrency());
        res = optlab::cpu_price(r, th > 0 ? th : 1);
    } else if (backend == 1) {
#ifdef OPTLAB_HAVE_CUDA
        if (optlab::gpu_count() == 0) throw std::runtime_error("no CUDA device");
        res = optlab::gpu_price(r);
#else
        throw std::runtime_error("this library was built without CUDA");
#endif
    } else {
        throw std::invalid_argument("backend must be 0 (CPU) or 1 (GPU)");
    }
    optlab_result out{};
    for (int k = 0; k < optlab::kOutputs; ++k) {
        out.price[k] = res.out[k].price;
        out.se[k] = res.out[k].se;
    }
    out.n_paths = res.n_paths;
    out.kernel_ms = res.kernel_ms;
    out.total_ms = res.total_ms;
    return out;
}

void paths(optlab_paths_request& rq, int backend, int n_threads) {
    const optlab::PathSpec<double> s = path_spec(rq);
    optlab::PathTiming t;
    if (backend == 0) {
        t = optlab::cpu_paths(s, n_threads);
    } else if (backend == 1) {
#ifdef OPTLAB_HAVE_CUDA
        if (optlab::gpu_count() == 0) throw std::runtime_error("no CUDA device");
        t = optlab::gpu_paths(s, rq.n_rows);
#else
        throw std::runtime_error("this library was built without CUDA");
#endif
    } else {
        throw std::invalid_argument("backend must be 0 (CPU) or 1 (GPU)");
    }
    rq.kernel_ms = t.kernel_ms, rq.total_ms = t.total_ms;
}

void leverage(optlab_leverage_request& rq, int backend, int n_threads) {
    if (!rq.dt || !rq.t0 || !rq.lv_lo || !rq.lv_inv_dx || !rq.lv_rows || !rq.x_grids || !rq.values)
        throw std::invalid_argument("null pointer");
    if (rq.n_steps < 1 || rq.n_pairs < 2 || rq.n_x < 2 || rq.n_bins < 2 || rq.n_fine < 16 || rq.n_lv < 2)
        throw std::invalid_argument("n_steps, n_pairs, n_x, n_bins, n_fine or n_lv too small");
    if (!(rq.kappa > 0 && rq.theta > 0 && rq.xi >= 0 && std::fabs(rq.rho) <= 1 && rq.v0 >= 0))
        throw std::invalid_argument("Heston parameters out of range");
    optlab::LeverageSpec s;
    s.n_steps = rq.n_steps, s.dt = rq.dt, s.t0 = rq.t0, s.n_pairs = rq.n_pairs, s.seed = rq.seed;
    s.v0 = rq.v0, s.kappa = rq.kappa, s.theta = rq.theta, s.xi = rq.xi, s.rho = rq.rho;
    s.n_x = rq.n_x, s.n_bins = rq.n_bins, s.n_fine = rq.n_fine, s.atm_vol = rq.atm_vol;
    s.bandwidth_scale = rq.bandwidth_scale, s.l_min = rq.l_min, s.l_max = rq.l_max, s.q_lo = rq.q_lo;
    s.q_hi = rq.q_hi, s.local_linear = rq.local_linear, s.positive_part = rq.positive_part, s.n_lv = rq.n_lv;
    s.lv_lo = rq.lv_lo, s.lv_inv_dx = rq.lv_inv_dx, s.lv_rows = rq.lv_rows, s.x_grids = rq.x_grids;
    s.values = rq.values;
    if (backend == 0) {
        rq.total_ms = optlab::cpu_leverage(s, n_threads);
    } else if (backend == 1) {
#ifdef OPTLAB_HAVE_CUDA
        if (optlab::gpu_count() == 0) throw std::runtime_error("no CUDA device");
        rq.total_ms = optlab::gpu_leverage(s);
#else
        throw std::runtime_error("this library was built without CUDA");
#endif
    } else {
        throw std::invalid_argument("backend must be 0 (CPU) or 1 (GPU)");
    }
}

bool device_info(int device, optlab_device& out) {
    std::memset(&out, 0, sizeof out);
#ifdef OPTLAB_HAVE_CUDA
    optlab::DeviceInfo d;
    if (!optlab::gpu_device_info(device, d)) return false;
    std::snprintf(out.name, sizeof out.name, "%s", d.name);
    out.major = d.major, out.minor = d.minor, out.sm_count = d.sm_count, out.clock_mhz = d.clock_mhz;
    out.total_mem = d.total_mem, out.free_mem = d.free_mem, out.integrated = d.integrated;
    out.kernel_timeout = d.kernel_timeout;
    return true;
#else
    (void)device;
    return false;
#endif
}

std::string gpu_name() {
#ifdef OPTLAB_HAVE_CUDA
    try {
        return optlab::gpu_count() > 0 ? optlab::gpu_device_name() : "";
    } catch (const std::exception&) {
        return "";
    }
#else
    return "";
#endif
}

}  // namespace optlab::api
