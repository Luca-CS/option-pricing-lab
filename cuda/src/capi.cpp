// C interface (include/optlab_cuda/capi.h): checks the request, converts it, and turns exceptions into an error code.
#include "optlab_cuda/capi.h"

#include <cmath>
#include <exception>
#include <stdexcept>
#include <string>
#include <thread>

#include "optlab_cuda/cpu_mc.hpp"
#include "optlab_cuda/gpu_mc.cuh"

namespace {

thread_local std::string g_error;
std::string g_name;

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

}  // namespace

extern "C" {

int optlab_gpu_count(void) { return optlab::gpu_count(); }

int optlab_price(const optlab_request* rq, int backend, int n_threads, optlab_result* out) {
    g_error.clear();
    try {
        if (!rq || !out) throw std::invalid_argument("null pointer");
        const optlab::McRequest r = convert(*rq);
        optlab::McResult res;
        if (backend == 0) {
            const int th = n_threads > 0 ? n_threads : static_cast<int>(std::thread::hardware_concurrency());
            res = optlab::cpu_price(r, th > 0 ? th : 1);
        } else if (backend == 1) {
            if (optlab::gpu_count() == 0) throw std::runtime_error("no CUDA device");
            res = optlab::gpu_price(r);
        } else {
            throw std::invalid_argument("backend must be 0 (CPU) or 1 (GPU)");
        }
        for (int k = 0; k < optlab::kOutputs; ++k) {
            out->price[k] = res.out[k].price;
            out->se[k] = res.out[k].se;
        }
        out->n_paths = res.n_paths;
        out->kernel_ms = res.kernel_ms;
        out->total_ms = res.total_ms;
        return 0;
    } catch (const std::exception& e) {
        g_error = e.what();
    } catch (...) {
        g_error = "unknown error";
    }
    return -1;
}

const char* optlab_last_error(void) { return g_error.c_str(); }

const char* optlab_gpu_name(void) {
    try {
        g_name = optlab::gpu_count() > 0 ? optlab::gpu_device_name() : "";
    } catch (const std::exception&) {
        g_name.clear();
    }
    return g_name.c_str();
}

}  // extern "C"
