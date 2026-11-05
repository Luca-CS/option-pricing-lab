// SLV leverage by the particle method (Guyon and Henry-Labordere 2012), the algorithm of `optlab.slv.calibrate_leverage`
// on the CPU or the GPU. The particles live on the device; at each step the device bins them (a fine histogram for the
// quantiles that bound the regression grid, and the binned sums of the kernel regression), the host turns the bins into
// E[v | x] on the grid and the leverage L = sqrt(sigma_loc^2 / E[v | x]), and the device moves the particles one step.
// Only the bins and the leverage row cross between host and device.
#pragma once

#include <cstdint>
#include <vector>

namespace optlab {

struct LeverageSpec {
    int n_steps = 0;
    const double* dt = nullptr;  // n_steps
    const double* t0 = nullptr;  // n_steps
    long long n_pairs = 0;       // particles = 2 n_pairs, antithetic
    std::uint64_t seed = 0;
    double v0 = 0, kappa = 0, theta = 0, xi = 0, rho = 0;  // xi already scaled by the mixing fraction
    int n_x = 81, n_bins = 400, n_fine = 8192;
    double atm_vol = 0.2, bandwidth_scale = 1.0, l_min = 0.05, l_max = 8.0, q_lo = 0.0005, q_hi = 0.9995;
    int local_linear = 0, positive_part = 0;
    // Dupire local variance at each step start, on uniform rows: lv_lo, lv_inv_dx (n_steps), lv_rows (n_steps x n_lv)
    int n_lv = 0;
    const double* lv_lo = nullptr;
    const double* lv_inv_dx = nullptr;
    const double* lv_rows = nullptr;
    double* x_grids = nullptr;  // out: n_steps x n_x
    double* values = nullptr;   // out: n_steps x n_x
};

// Bins of one step, filled by a device: fine counts on [lo, hi] for the quantiles, and the five sums of the
// regression on n_bins bins of the same range (counts, v, x, x^2, x v).
struct Bins {
    std::vector<long long> fine;
    std::vector<double> acc;  // 5 x n_bins
    double v_sum = 0.0;
};

// What a device does on its particles.
class ParticleDevice {
public:
    virtual ~ParticleDevice() = default;
    virtual void bin(double lo, double hi, Bins& out) = 0;  // lo, hi: range of x at this step
    // One step with the leverage row (uniform x_eval from a to b, n_x values); returns the new range of x.
    virtual void step(int i, double dt, double a, double b, const std::vector<double>& lev, double& lo, double& hi) = 0;
};

// The host side of the algorithm, the same for every device. Returns wall-clock milliseconds.
double calibrate_leverage(const LeverageSpec& s, ParticleDevice& dev);

double cpu_leverage(const LeverageSpec& s, int n_threads);
double gpu_leverage(const LeverageSpec& s);  // throws std::runtime_error

}  // namespace optlab
