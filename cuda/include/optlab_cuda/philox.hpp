// Counter-based normals shared by the CPU and the GPU: Philox4x32-10 (Salmon et al., "Parallel random numbers: as
// easy as 1, 2, 3", SC 2011) and Box-Muller. A draw is a pure function of (seed, pair, step, stream), so a path does
// not depend on the thread that computes it, on the batch it falls in, or on the device: the CPU and the GPU see the
// same normals, up to the rounding of float against double in Box-Muller.
#pragma once

#include <cmath>
#include <cstdint>

#ifdef __CUDACC__
#define OPTLAB_PHX __host__ __device__ __forceinline__
#else
#define OPTLAB_PHX inline
#endif

namespace optlab {

struct U4 {
    std::uint32_t x, y, z, w;
};

OPTLAB_PHX void mulhilo32(std::uint32_t a, std::uint32_t b, std::uint32_t& hi, std::uint32_t& lo) {
#ifdef __CUDA_ARCH__
    hi = __umulhi(a, b);
    lo = a * b;
#else
    const std::uint64_t p = static_cast<std::uint64_t>(a) * b;
    hi = static_cast<std::uint32_t>(p >> 32);
    lo = static_cast<std::uint32_t>(p);
#endif
}

OPTLAB_PHX U4 philox4x32_10(U4 c, std::uint32_t k0, std::uint32_t k1) {
    for (int r = 0; r < 10; ++r) {
        std::uint32_t hi0, lo0, hi1, lo1;
        mulhilo32(0xD2511F53u, c.x, hi0, lo0);
        mulhilo32(0xCD9E8D57u, c.z, hi1, lo1);
        c = {hi1 ^ c.y ^ k0, lo1, hi0 ^ c.w ^ k1, lo0};
        k0 += 0x9E3779B9u;
        k1 += 0xBB67AE85u;
    }
    return c;
}

// Uniform in (0, 1): u = (i + 1/2) 2^-32, never 0 or 1, so the logarithm below is finite (|z| <= 6.66).
template <typename R>
OPTLAB_PHX R to_unit(std::uint32_t i) {
    return static_cast<R>(i) * static_cast<R>(2.3283064365386963e-10) + static_cast<R>(1.1641532182693481e-10);
}

template <typename R>
OPTLAB_PHX void box_muller(std::uint32_t a, std::uint32_t b, R& z0, R& z1) {
    const R u = to_unit<R>(a), v = to_unit<R>(b);
#ifdef __CUDA_ARCH__
    if constexpr (sizeof(R) == 4) {
        const float r = sqrtf(-2.0f * logf(u));
        float s, c;
        sincospif(2.0f * v, &s, &c);
        z0 = r * c, z1 = r * s;
        return;
    } else {
        const double r = sqrt(-2.0 * log(u));
        double s, c;
        sincospi(2.0 * v, &s, &c);
        z0 = r * c, z1 = r * s;
        return;
    }
#else
    const R r = std::sqrt(R(-2) * std::log(u));
    const R a2 = R(6.283185307179586) * v;
    z0 = r * std::cos(a2), z1 = r * std::sin(a2);
#endif
}

// Four normals for (pair, step, stream, block): block counts groups of four within a stream.
template <typename R>
OPTLAB_PHX void normals4(std::uint64_t pair, std::uint32_t step, std::uint32_t stream_block, std::uint32_t k0,
                         std::uint32_t k1, R out[4]) {
    const U4 r = philox4x32_10({static_cast<std::uint32_t>(pair), static_cast<std::uint32_t>(pair >> 32), step,
                                stream_block}, k0, k1);
    box_muller<R>(r.x, r.y, out[0], out[1]);
    box_muller<R>(r.z, r.w, out[2], out[3]);
}

}  // namespace optlab
