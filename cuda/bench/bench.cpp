// Benchmark protocol of the handoff (section 6), mission M5.
#include <cstdio>

#include "optlab_cuda/gpu_mc.cuh"

int main() {
    std::printf("%s\nbenchmarks: mission M5\n", optlab::gpu_device_name().c_str());
    return 0;
}
