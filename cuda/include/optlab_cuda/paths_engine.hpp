// Entry points of the path engine (paths.hpp) on each device. The specification is always given in host memory and
// double precision; the GPU copies it to the device in float.
#pragma once

#include "paths.hpp"

namespace optlab {

struct PathTiming {
    double kernel_ms = 0.0;  // compute (GPU: CUDA events, CPU: wall time of the workers)
    double total_ms = 0.0;   // end to end, copies included
};

// Every pair of `s` on n_threads threads (0: all hardware threads).
PathTiming cpu_paths(const PathSpec<double>& s, int n_threads);

// Same on the GPU, in float, in launches short enough for a display GPU (Windows resets a kernel after 2 s), with
// the output copied back to s.out. n_rows is the number of table rows. Throws std::runtime_error.
PathTiming gpu_paths(const PathSpec<double>& s, int n_rows);

struct DeviceInfo {
    char name[256] = {};
    int major = 0, minor = 0, sm_count = 0, clock_mhz = 0;
    long long total_mem = 0, free_mem = 0;
    int integrated = 0, kernel_timeout = 0;  // kernel_timeout: a watchdog limits kernels (display GPU)
};

bool gpu_device_info(int device, DeviceInfo& info);

}  // namespace optlab
