// optlab.native._ext: the engine's checked requests (cuda/include/optlab_cuda/requests.hpp) from Python, by nanobind.
//
// NumPy arrays reach the engine without a copy, and their sizes are checked here against those the request implies,
// so a wrong array is an error rather than a read out of bounds. Output arrays are never converted (a converted copy
// would take the results away): they must be C-contiguous float64, as optlab.native allocates them. The GIL is released while the engine runs, as ctypes
// did: the batch engine of optlab.structured evaluates one batch on Python threads while the next one simulates.
// An engine error (a bad request, no device, a CUDA failure) raises optlab.core.engines.EngineError with the engine's
// message: the engine runs inside the Python process, so it never exits.
#include <array>
#include <cstdint>
#include <exception>
#include <stdexcept>
#include <string>
#include <tuple>

#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/array.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>

#include "optlab_cuda/requests.hpp"

namespace nb = nanobind;
using namespace nb::literals;

namespace {

template <class T>
using In = nb::ndarray<const T, nb::c_contig, nb::device::cpu>;
template <class T>
using Out = nb::ndarray<T, nb::c_contig, nb::device::cpu>;

template <class A>
void need(const A& a, std::size_t n, const char* what) {
    if (a.size() != n)
        throw std::invalid_argument(std::string(what) + ": expected " + std::to_string(n) + " values, got " +
                                    std::to_string(a.size()));
}

PyObject* engine_error = nullptr;  // optlab.core.engines.EngineError, kept for the life of the module

void translate(const std::exception_ptr& p, void*) {
    try {
        std::rethrow_exception(p);
    } catch (const nb::builtin_exception&) {
        throw;  // nanobind's own (TypeError, ...): not the engine's
    } catch (const std::invalid_argument& e) {
        PyErr_SetString(engine_error, e.what());
    } catch (const std::runtime_error& e) {
        PyErr_SetString(engine_error, e.what());
    }
}

}  // namespace

NB_MODULE(_ext, m) {
    m.doc() = "The compiled engine of optlab (C++, CUDA when built with it): fixed products, paths, SLV leverage.";
    nb::object err = nb::module_::import_("optlab.core.engines").attr("EngineError");
    engine_error = err.release().ptr();
    nb::register_exception_translator(translate);

    m.def("gpu_count", &optlab::api::gpu_count, "CUDA devices; 0 without device, driver or CUDA build.");
    m.def("has_cuda", &optlab::api::has_cuda, "Whether this module was built with the CUDA backend.");
    m.def("cpu_threads", &optlab::api::cpu_threads, "Hardware threads seen by the engine.");
    m.def("gpu_name", &optlab::api::gpu_name, "Device 0 as 'name (sm_XY, N SM)', or '' without device.");

    m.def(
        "device_info",
        [](int device) -> nb::object {
            optlab_device d;
            if (!optlab::api::device_info(device, d)) return nb::none();
            nb::dict out;
            out["name"] = std::string(d.name);
            out["major"] = d.major, out["minor"] = d.minor, out["sm_count"] = d.sm_count;
            out["clock_mhz"] = d.clock_mhz, out["total_mem"] = d.total_mem, out["free_mem"] = d.free_mem;
            out["integrated"] = d.integrated, out["kernel_timeout"] = d.kernel_timeout;
            return out;
        },
        "device"_a, "The properties of a CUDA device as a dict, or None without it.");

    m.def(
        "price",
        [](int model, int product, std::array<int, 3> legs, std::array<double, 3> strikes, double H, int n_steps,
           double S0, double r, double q, double T, double sigma, double v0, double kappa, double theta, double xi,
           double rho, long long n_paths, std::uint64_t seed, int backend, int n_threads) {
            optlab_request rq{};
            rq.model = model, rq.product = product, rq.H = H, rq.n_steps = n_steps;
            for (int k = 0; k < 3; ++k) rq.legs[k] = legs[k], rq.strikes[k] = strikes[k];
            rq.S0 = S0, rq.r = r, rq.q = q, rq.T = T, rq.sigma = sigma;
            rq.v0 = v0, rq.kappa = kappa, rq.theta = theta, rq.xi = xi, rq.rho = rho;
            rq.n_paths = n_paths, rq.seed = seed;
            optlab_result out;
            {
                nb::gil_scoped_release unlocked;
                out = optlab::api::price(rq, backend, n_threads);
            }
            std::array<double, 4> price{}, se{};
            for (int k = 0; k < 4; ++k) price[k] = out.price[k], se[k] = out.se[k];
            return std::make_tuple(price, se, out.n_paths, out.kernel_ms, out.total_ms);
        },
        "model"_a, "product"_a, "legs"_a, "strikes"_a, "H"_a, "n_steps"_a, "S0"_a, "r"_a, "q"_a, "T"_a, "sigma"_a,
        "v0"_a, "kappa"_a, "theta"_a, "xi"_a, "rho"_a, "n_paths"_a, "seed"_a, "backend"_a, "n_threads"_a = 0,
        "A fixed product (model 0 Black-Scholes, 1 Heston; product 0 European, 1 Asian, 2 down-and-out barrier) on "
        "backend 0 (CPU, double) or 1 (GPU, float): (price[4], se[4], n_paths, kernel_ms, total_ms).");

    m.def(
        "paths",
        [](In<double> dt, In<double> t0, In<int> obs_step, In<double> root, In<int> kind, In<double> cvar,
           In<double> heston, int same, In<int> step_row, In<double> row_lo, In<double> row_inv_dx, In<double> table,
           In<double> fwd, int mixer, std::array<double, 8> mix, In<double> low_root, int n_x, long long n_pairs,
           long long first_pair, std::uint64_t seed, In<int> col_of, int n_keep, In<int> win_lo, In<int> win_hi,
           Out<double> out, int backend, int n_threads) {
            const std::size_t k = kind.size(), steps = dt.size(), obs = obs_step.size(), rows = row_lo.size();
            const std::size_t windows = win_lo.size();
            need(t0, steps, "t0"), need(root, k * k, "root"), need(cvar, k, "cvar"), need(heston, k * 5, "heston");
            need(fwd, k * obs, "fwd"), need(col_of, obs, "col_of"), need(win_hi, windows, "win_hi");
            need(row_inv_dx, rows, "row_inv_dx");
            if (rows) need(step_row, k * steps, "step_row"), need(table, rows * static_cast<std::size_t>(n_x), "table");
            if (mixer) need(low_root, k * k, "low_root");
            const std::size_t cols = static_cast<std::size_t>(n_keep) + 2 * windows;
            need(out, k * cols * 2 * static_cast<std::size_t>(n_pairs), "out");
            optlab_paths_request rq{};
            rq.n_names = static_cast<int>(k), rq.n_steps = static_cast<int>(steps), rq.n_obs = static_cast<int>(obs);
            rq.n_x = n_x, rq.n_rows = static_cast<int>(rows), rq.n_pairs = n_pairs, rq.first_pair = first_pair;
            rq.seed = seed, rq.dt = dt.data(), rq.t0 = t0.data(), rq.obs_step = obs_step.data(), rq.root = root.data();
            rq.kind = kind.data(), rq.cvar = cvar.data(), rq.heston = heston.data(), rq.same = same;
            rq.step_row = rows ? step_row.data() : nullptr, rq.row_lo = rows ? row_lo.data() : nullptr;
            rq.row_inv_dx = rows ? row_inv_dx.data() : nullptr, rq.table = rows ? table.data() : nullptr;
            rq.fwd = fwd.data(), rq.mixer = mixer, rq.low_root = mixer ? low_root.data() : nullptr;
            for (int i = 0; i < 8; ++i) rq.mix[i] = mix[i];
            rq.n_cols = static_cast<int>(cols), rq.n_keep = n_keep, rq.col_of = col_of.data();
            rq.n_windows = static_cast<int>(windows), rq.win_lo = windows ? win_lo.data() : nullptr;
            rq.win_hi = windows ? win_hi.data() : nullptr, rq.out = out.data();
            {
                nb::gil_scoped_release unlocked;
                optlab::api::paths(rq, backend, n_threads);
            }
            return std::make_tuple(rq.kernel_ms, rq.total_ms);
        },
        "dt"_a, "t0"_a, "obs_step"_a, "root"_a, "kind"_a, "cvar"_a, "heston"_a, "same"_a, "step_row"_a, "row_lo"_a,
        "row_inv_dx"_a, "table"_a, "fwd"_a, "mixer"_a, "mix"_a, "low_root"_a, "n_x"_a, "n_pairs"_a, "first_pair"_a,
        "seed"_a, "col_of"_a, "n_keep"_a, "win_lo"_a, "win_hi"_a, nb::arg("out").noconvert(), "backend"_a, "n_threads"_a = 0,
        "Paths of 1 to 8 names (paths.hpp) into `out` (names x columns x 2 n_pairs): (kernel_ms, total_ms). Empty "
        "arrays stand for the parts a request does not use (no table, no local correlation, no window).");

    m.def(
        "leverage",
        [](In<double> dt, In<double> t0, long long n_pairs, std::uint64_t seed, double v0, double kappa, double theta,
           double xi, double rho, int n_bins, int n_fine, double atm_vol, double bandwidth_scale, double l_min,
           double l_max, double q_lo, double q_hi, int local_linear, int positive_part, In<double> lv_lo,
           In<double> lv_inv_dx, In<double> lv_rows, Out<double> x_grids, Out<double> values, int backend,
           int n_threads) {
            const std::size_t steps = dt.size(), n_lv = steps ? lv_rows.size() / steps : 0;
            need(t0, steps, "t0"), need(lv_lo, steps, "lv_lo"), need(lv_inv_dx, steps, "lv_inv_dx");
            need(lv_rows, steps * n_lv, "lv_rows");
            const std::size_t n_x = steps ? x_grids.size() / steps : 0;
            need(x_grids, steps * n_x, "x_grids"), need(values, steps * n_x, "values");
            optlab_leverage_request rq{};
            rq.n_steps = static_cast<int>(steps), rq.dt = dt.data(), rq.t0 = t0.data();
            rq.n_pairs = n_pairs, rq.seed = seed;
            rq.v0 = v0, rq.kappa = kappa, rq.theta = theta, rq.xi = xi, rq.rho = rho;
            rq.n_x = static_cast<int>(n_x), rq.n_bins = n_bins, rq.n_fine = n_fine;
            rq.atm_vol = atm_vol, rq.bandwidth_scale = bandwidth_scale, rq.l_min = l_min, rq.l_max = l_max;
            rq.q_lo = q_lo, rq.q_hi = q_hi, rq.local_linear = local_linear, rq.positive_part = positive_part;
            rq.n_lv = static_cast<int>(n_lv), rq.lv_lo = lv_lo.data(), rq.lv_inv_dx = lv_inv_dx.data();
            rq.lv_rows = lv_rows.data(), rq.x_grids = x_grids.data(), rq.values = values.data();
            {
                nb::gil_scoped_release unlocked;
                optlab::api::leverage(rq, backend, n_threads);
            }
            return rq.total_ms;
        },
        "dt"_a, "t0"_a, "n_pairs"_a, "seed"_a, "v0"_a, "kappa"_a, "theta"_a, "xi"_a, "rho"_a, "n_bins"_a, "n_fine"_a,
        "atm_vol"_a, "bandwidth_scale"_a, "l_min"_a, "l_max"_a, "q_lo"_a, "q_hi"_a, "local_linear"_a, "positive_part"_a,
        "lv_lo"_a, "lv_inv_dx"_a, "lv_rows"_a, nb::arg("x_grids").noconvert(), nb::arg("values").noconvert(), "backend"_a,
        "n_threads"_a = 0,
        "The SLV leverage by the particle method (leverage.hpp) into x_grids and values (steps x n_x): total_ms.");
}
