// Parameters and results shared by the CPU reference and the GPU engine.
#pragma once

#include <array>
#include <cstdint>

namespace optlab {

struct MarketParams {
    double S0 = 100.0, r = 0.04, q = 0.01, T = 1.0;
};

struct BSParams {
    double sigma = 0.20;
};

// SPX calibration of results/spx_calibration.json (Feller ratio 0.19).
struct HestonParams {
    double v0 = 0.022846642482989885, kappa = 3.0892635624407627, theta = 0.05372458999932075,
           xi = 1.307745170926575, rho = -0.6879606734541107;
};

enum class Model { BlackScholes, Heston };
enum class Product { European, Asian, Barrier };
enum class Leg { None, Call, Put, Forward };  // Forward: S_T - K, for put-call parity on the same paths

// Outputs, all estimated on the same antithetic pairs:
//   European: out[k] = discounted payoff of legs[k], k = 0..2
//   Asian:    out[0] = arithmetic, out[1] = geometric, both of legs[0] on the fixings
//   Barrier:  out[0] = down-and-out of legs[0] (knocked out if S_ti <= H, i = 1..n), out[1] = same leg, no barrier
//   always:   out[3] = S_T, undiscounted (martingale check: E[S_T] = S0 exp((r - q) T))
constexpr int kOutputs = 4;

struct ProductParams {
    Product product = Product::European;
    std::array<Leg, 3> legs{Leg::Call, Leg::None, Leg::None};
    std::array<double, 3> strikes{100.0, 100.0, 100.0};
    double H = 80.0;
    int n_steps = 1;  // time steps of T / n_steps; Asian: one fixing per step
};

struct McRequest {
    Model model = Model::BlackScholes;
    MarketParams market;
    BSParams bs;
    HestonParams heston;
    ProductParams product;
    long long n_paths = 1'000'000;  // even: n_paths / 2 antithetic pairs
    std::uint64_t seed = 20261029;
};

struct Estimate {
    double price = 0.0, se = 0.0;
};

struct McResult {
    std::array<Estimate, kOutputs> out{};
    long long n_paths = 0;
    double kernel_ms = 0.0;  // GPU: kernel time (CUDA events); CPU: compute time
    double total_ms = 0.0;   // end to end (allocation, launches, copy back)
};

// Estimate from sums over n_pairs pair averages y = (pay(Z) + pay(-Z)) / 2.
Estimate estimate_from_sums(double sum, double sumsq, long long n_pairs);

}  // namespace optlab
