// CPU reference in double precision: std::mt19937_64 and std::normal_distribution<double>, same schemes as the GPU.
#pragma once

#include "params.hpp"

namespace optlab {

// n_threads = 1 is the single-thread reference; with more threads the pairs are split into contiguous blocks,
// each with its own generator seeded by (seed, thread index), so the price depends on n_threads.
McResult cpu_price(const McRequest& rq, int n_threads = 1);

}  // namespace optlab
