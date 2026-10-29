// GPU engine: host functions that launch the kernels. Host-only declarations, includable from .cpp files.
#pragma once

#include <string>

#include "params.hpp"

namespace optlab {

struct GpuConfig {
    int block = 256;            // threads per block
    int grid = 0;               // 0: number of SMs x active blocks per SM (cudaOccupancyMaxActiveBlocksPerMultiprocessor)
    bool use_double = false;    // path state in double instead of float (same normals)
    double max_work = 2.5e8;    // path-steps per launch, keeps each launch well under the Windows TDR limit (2 s)
};

struct GpuLaunchInfo {
    int block = 0, grid = 0, blocks_per_sm = 0, launches = 0;
};

// Prices on the GPU. Pair p (or group of four pairs for the Black-Scholes European) draws its normals from Philox
// subsequence p of `seed`, so the result depends only on (seed, n_paths), not on the launch configuration.
McResult gpu_price(const McRequest& rq, const GpuConfig& cfg = {}, GpuLaunchInfo* info = nullptr);

std::string gpu_device_name();

}  // namespace optlab
