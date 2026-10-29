// GPU engine: host functions that launch the kernels. Host-only declarations, includable from .cpp files.
#pragma once

#include <string>

#include "params.hpp"

namespace optlab {

// Kernel variants of the optimisation study (handoff, M6). Default: the production kernel. Each switch changes
// one thing; the non-default variants are compiled for the Black-Scholes European and the two barriers only.
struct GpuVariant {
    bool fast_intrinsics = false;  // __expf / __logf instead of expf / logf
    bool antithetic = true;        // false: plain paths, same N
    bool double_sums = false;      // per-thread sums in double instead of compensated (Kahan) float
    bool normal1 = false;          // curand_normal (one at a time) instead of curand_normal4
    bool per_thread_rng = false;   // one Philox state per thread, initialised once (single launch only)
    bool min_blocks4 = false;      // __launch_bounds__(256, 4): at most 64 registers, 4 blocks of 256 per SM
};

struct GpuConfig {
    int block = 512;            // threads per block (multiple of 32, at most 512; 256 with min_blocks4). 512 is
                                // 6 % faster than 256 on Heston QE and 5 % on the BS European (bench/ablation.cpp)
    int grid = 0;               // 0: number of SMs x active blocks per SM (cudaOccupancyMaxActiveBlocksPerMultiprocessor)
    bool use_double = false;    // path state in double instead of float (same normals)
    double max_work = 2.5e8;    // path-steps per launch, keeps each launch well under the Windows TDR limit (2 s)
    GpuVariant variant;
};

struct GpuLaunchInfo {
    int block = 0, grid = 0, blocks_per_sm = 0, launches = 0, registers = 0;
    double kernel2_ms = 0.0;    // stored-paths variant: time of the payoff kernel (kernel_ms is the total)
};

// Prices on the GPU. Pair p (or group of four pairs for the Black-Scholes European) draws its normals from Philox
// subsequence p of `seed`, so the result depends only on (seed, n_paths), not on the launch configuration.
// The first call creates the CUDA context; later calls reuse a cached accumulator buffer and events.
McResult gpu_price(const McRequest& rq, const GpuConfig& cfg = {}, GpuLaunchInfo* info = nullptr);

// Barrier products only: a first kernel writes every path (float log-returns, n_paths x n_steps) to global
// memory, a second computes the payoff from them. For comparison with the fused kernel; same normals.
McResult gpu_price_stored(const McRequest& rq, const GpuConfig& cfg = {}, GpuLaunchInfo* info = nullptr);

std::string gpu_device_name();

// Number of CUDA devices; 0 when there is no device or no driver (never throws).
int gpu_count();

}  // namespace optlab
