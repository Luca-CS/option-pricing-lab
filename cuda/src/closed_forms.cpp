#include "optlab_cuda/closed_forms.hpp"

#include <cmath>

namespace optlab {

double norm_cdf(double x) { return 0.5 * std::erfc(-x / std::sqrt(2.0)); }

double bs_price(double S, double K, double T, double r, double q, double sigma, bool call) {
    const double sd = sigma * std::sqrt(T);
    const double d1 = (std::log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / sd, d2 = d1 - sd;
    const double fwd = S * std::exp(-q * T), dk = K * std::exp(-r * T);
    return call ? fwd * norm_cdf(d1) - dk * norm_cdf(d2) : dk * norm_cdf(-d2) - fwd * norm_cdf(-d1);
}

double geometric_asian_price(double S, double K, double T, double r, double q, double sigma, int n, bool call) {
    // ln G = ln S + mean_i y_i, Gaussian with mean (r - q - sigma^2/2) mean(t_i), variance sigma^2 mean_ij min(t_i, t_j)
    double mean_t = 0.0, mean_min = 0.0;
    for (int i = 1; i <= n; ++i) {
        mean_t += i * T / n;
        for (int j = 1; j <= n; ++j) mean_min += (i < j ? i : j) * T / n;
    }
    mean_t /= n;
    mean_min /= double(n) * n;
    const double mu = std::log(S) + (r - q - 0.5 * sigma * sigma) * mean_t, sd = sigma * std::sqrt(mean_min);
    const double d2 = (mu - std::log(K)) / sd, d1 = d2 + sd, eg = std::exp(mu + 0.5 * sd * sd), D = std::exp(-r * T);
    return call ? D * (eg * norm_cdf(d1) - K * norm_cdf(d2)) : D * (K * norm_cdf(-d2) - eg * norm_cdf(-d1));
}

double down_out_call(double S, double K, double H, double T, double r, double q, double sigma) {
    const double sd = sigma * std::sqrt(T), lam = (r - q + 0.5 * sigma * sigma) / (sigma * sigma);
    const double y = std::log(H * H / (S * K)) / sd + lam * sd;
    const double down_in = S * std::exp(-q * T) * std::pow(H / S, 2 * lam) * norm_cdf(y) -
                           K * std::exp(-r * T) * std::pow(H / S, 2 * lam - 2) * norm_cdf(y - sd);
    return bs_price(S, K, T, r, q, sigma, true) - down_in;
}

double down_out_call_bgk(double S, double K, double H, double T, double r, double q, double sigma, int n_monitor) {
    const double shifted = H * std::exp(-0.5826 * sigma * std::sqrt(T / n_monitor));
    return down_out_call(S, K, shifted, T, r, q, sigma);
}

}  // namespace optlab
