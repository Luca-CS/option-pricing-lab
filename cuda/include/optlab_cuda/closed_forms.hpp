// Closed forms used as references by the tests.
#pragma once

namespace optlab {

double norm_cdf(double x);

// Black-Scholes European, continuous dividend yield q.
double bs_price(double S, double K, double T, double r, double q, double sigma, bool call);

// Geometric Asian call or put on n equally spaced fixings t_i = i T / n, i = 1..n, paid at T.
double geometric_asian_price(double S, double K, double T, double r, double q, double sigma, int n, bool call);

// Down-and-out call, H < K, continuous monitoring (Reiner-Rubinstein).
double down_out_call(double S, double K, double H, double T, double r, double q, double sigma);

// Same with daily monitoring approximated by the Broadie-Glasserman-Kou shift H exp(-0.5826 sigma sqrt(dt)).
double down_out_call_bgk(double S, double K, double H, double T, double r, double q, double sigma, int n_monitor);

}  // namespace optlab
