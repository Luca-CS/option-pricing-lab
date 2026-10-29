// Monte Carlo kernels. One thread prices one antithetic pair at a time (grid-stride loop), path state in float
// (or double for comparison), per-thread sums in compensated float (Kahan: FP64 runs at 1/32 rate on Turing and two
// double adds per output made the Black-Scholes European 5.4x slower), converted to double for the warp-shuffle
// and shared-memory reduction, then one atomicAdd(double) per block and per accumulator. The optimisation variants
// of bench/ablation.cpp are compile-time options (KOpt).
#include "optlab_cuda/gpu_mc.cuh"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include <type_traits>
#include <vector>

#include <cuda_runtime.h>
#include <curand_kernel.h>

#include "optlab_cuda/schemes.hpp"

#define CUDA_CHECK(call)                                                                                 \
    do {                                                                                                 \
        const cudaError_t err_ = (call);                                                                 \
        if (err_ != cudaSuccess) {                                                                       \
            std::fprintf(stderr, "CUDA error %s:%d: %s\n", __FILE__, __LINE__, cudaGetErrorString(err_)); \
            std::exit(1);                                                                                \
        }                                                                                                \
    } while (0)

namespace optlab {
namespace {

constexpr int kAcc = 2 * kOutputs;  // sum and sum of squares of each output
constexpr int kMaxBlock = 512;

template <bool FastMath = false, bool Antithetic = true, bool KahanFloat = true, bool Normal4 = true,
          bool PerPairRng = true, int MinBlocks = 1>
struct KOpt {
    static constexpr bool fast_math = FastMath;
    static constexpr int legs = Antithetic ? 2 : 1;
    static constexpr bool kahan_float = KahanFloat;
    static constexpr bool normal4 = Normal4;
    static constexpr bool per_pair_rng = PerPairRng;
    static constexpr int max_threads = MinBlocks > 1 ? 256 : kMaxBlock;
    static constexpr int min_blocks = MinBlocks;
};
using Default = KOpt<>;

// ---------------------------------------------------------------------------------------------- normals
template <bool Normal4>
struct PhiloxNormals {
    curandStatePhilox4_32_10_t st;
    __device__ float4 draw4() {
        if constexpr (Normal4) return curand_normal4(&st);
        float4 n;
        n.x = curand_normal(&st), n.y = curand_normal(&st), n.z = curand_normal(&st), n.w = curand_normal(&st);
        return n;
    }
    template <typename R>  // float normals in both cases: float and double paths differ by arithmetic only
    __device__ void next4(R z[4]) {
        const float4 n = draw4();
        z[0] = n.x, z[1] = n.y, z[2] = n.z, z[3] = n.w;
    }
};

// ---------------------------------------------------------------------------------------------- per-thread sums
struct AccDouble {
    double v[kAcc] = {};
    __device__ void add(int k, float x) { v[k] += static_cast<double>(x); }
    __device__ void add(int k, double x) { v[k] += x; }
    __device__ double get(int k) const { return v[k]; }
};

struct AccKahan {  // compensated float sums: about 2^-24 relative error, no FP64 in the loop
    float s[kAcc] = {}, c[kAcc] = {};
    __device__ void add(int k, float x) {
        const float y = x - c[k], t = s[k] + y;
        c[k] = (t - s[k]) - y;
        s[k] = t;
    }
    __device__ double get(int k) const { return static_cast<double>(s[k]) - static_cast<double>(c[k]); }
};

template <typename R, typename Acc>
__device__ __forceinline__ void accumulate(const R out[kOutputs], Acc& acc) {
    for (int k = 0; k < kOutputs; ++k) {
        acc.add(k, out[k]);
        acc.add(kOutputs + k, out[k] * out[k]);
    }
}

// Block size must be a multiple of 32.
__device__ void block_reduce_add(double v[kAcc], double* global_acc) {
    __shared__ double sh[kAcc][32];
    const int lane = threadIdx.x & 31, warp = threadIdx.x >> 5, n_warps = blockDim.x >> 5;
    for (int k = 0; k < kAcc; ++k) {
        for (int off = 16; off > 0; off >>= 1) v[k] += __shfl_down_sync(0xffffffffu, v[k], off);
        if (lane == 0) sh[k][warp] = v[k];
    }
    __syncthreads();
    if (warp == 0) {
        for (int k = 0; k < kAcc; ++k) {
            double x = lane < n_warps ? sh[k][lane] : 0.0;
            for (int off = 16; off > 0; off >>= 1) x += __shfl_down_sync(0xffffffffu, x, off);
            if (lane == 0) atomicAdd(&global_acc[k], x);
        }
    }
}

// ---------------------------------------------------------------------------------------------- fused kernel
// Work items [begin, end): one unit each (antithetic pair, or single path), or four units for the Black-Scholes
// European (one curand_normal4 call serves four one-step units). Item i uses Philox subsequence i; with
// per_thread_rng, thread t uses subsequence t for all its items instead.
template <typename R, Model M, Product P, typename Opt>
__global__ void __launch_bounds__(Opt::max_threads, Opt::min_blocks)
    mc_kernel(PathConsts<R> c, unsigned long long seed, long long begin, long long end, long long n_units,
              double* global_acc) {
    using Acc = std::conditional_t<Opt::kahan_float && std::is_same_v<R, float>, AccKahan, AccDouble>;
    Acc acc;
    R out[kOutputs];
    const long long tid = static_cast<long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    const long long stride = static_cast<long long>(gridDim.x) * blockDim.x;
    PhiloxNormals<Opt::normal4> src;
    if constexpr (!Opt::per_pair_rng) curand_init(seed, static_cast<unsigned long long>(tid), 0ull, &src.st);
    for (long long it = begin + tid; it < end; it += stride) {
        if constexpr (Opt::per_pair_rng) curand_init(seed, static_cast<unsigned long long>(it), 0ull, &src.st);
        if constexpr (M == Model::BlackScholes && P == Product::European) {
            R z[4];
            src.next4(z);
            for (int j = 0; j < 4; ++j) {
                if (4 * it + j >= n_units) break;
                bs_european_unit<R, Opt>(c, z[j], out);
                accumulate(out, acc);
            }
        } else {
            path_unit<R, M, P, Opt>(c, src, out);
            accumulate(out, acc);
        }
    }
    double v[kAcc];
    for (int k = 0; k < kAcc; ++k) v[k] = acc.get(k);
    block_reduce_add(v, global_acc);
}

// ---------------------------------------------------------------------------------------------- stored paths
// Same normals as the fused kernel (subsequence = pair index, same consumption order).
template <Model M>
__global__ void store_kernel(PathConsts<float> c, unsigned long long seed, long long n_pairs, float* paths) {
    const long long n_paths = 2 * n_pairs;
    for (long long it = static_cast<long long>(blockIdx.x) * blockDim.x + threadIdx.x; it < n_pairs;
         it += static_cast<long long>(gridDim.x) * blockDim.x) {
        PhiloxNormals<true> src;
        curand_init(seed, static_cast<unsigned long long>(it), 0ull, &src.st);
        float y[2] = {0.f, 0.f}, z[4];
        [[maybe_unused]] float v[2] = {c.v0, c.v0};  // Heston only
        constexpr int per_step = M == Model::Heston ? 2 : 1;
        for (int i = 0; i < c.n_steps;) {
            src.next4(z);
            for (int j = 0; j + per_step <= 4 && i < c.n_steps; j += per_step, ++i) {
                for (int a = 0; a < 2; ++a) {
                    const float sg = a == 0 ? 1.f : -1.f;
                    if constexpr (M == Model::Heston)
                        qe_step<float>(c, y[a], v[a], sg * z[j], sg * z[j + 1]);
                    else
                        y[a] += c.bs_drift + c.bs_vol * sg * z[j];
                    paths[static_cast<long long>(i) * n_paths + it + a * n_pairs] = y[a];  // step-major: coalesced
                }
            }
        }
    }
}

__global__ void stored_barrier_kernel(PathConsts<float> c, long long n_pairs, const float* paths, double* global_acc) {
    const long long n_paths = 2 * n_pairs;
    AccDouble acc;
    for (long long it = static_cast<long long>(blockIdx.x) * blockDim.x + threadIdx.x; it < n_pairs;
         it += static_cast<long long>(gridDim.x) * blockDim.x) {
        float o[kOutputs] = {0.f, 0.f, 0.f, 0.f};
        for (int a = 0; a < 2; ++a) {
            bool alive = true;
            float y = 0.f;
            for (int i = 0; i < c.n_steps; ++i) {
                y = paths[static_cast<long long>(i) * n_paths + it + a * n_pairs];
                alive = alive && !(y <= c.ln_h);
            }
            const float S = c.S0 * m_exp(y), pay = c.D * leg_payoff(c.leg[0], S, c.K[0]);
            o[0] += alive ? pay : 0.f;
            o[1] += pay;
            o[3] += S;
        }
        for (int k = 0; k < kOutputs; ++k) o[k] *= 0.5f;
        accumulate(o, acc);
    }
    block_reduce_add(acc.v, global_acc);
}

// ---------------------------------------------------------------------------------------------- host side
// Cached across calls (single host thread): the accumulators and the timing events.
struct Context {
    double* acc = nullptr;
    cudaEvent_t e0{}, e1{}, e2{};
    int n_sm = 0;
    Context() {
        int device = 0;
        CUDA_CHECK(cudaGetDevice(&device));
        CUDA_CHECK(cudaDeviceGetAttribute(&n_sm, cudaDevAttrMultiProcessorCount, device));
        CUDA_CHECK(cudaMalloc(&acc, kAcc * sizeof(double)));
        CUDA_CHECK(cudaEventCreate(&e0));
        CUDA_CHECK(cudaEventCreate(&e1));
        CUDA_CHECK(cudaEventCreate(&e2));
    }
};

Context& ctx() {
    static Context c;
    return c;
}

// Occupancy and registers of one kernel at one block size, cached (kernels sharing a signature share the cache
// slot, hence the kernel pointer in the key).
template <typename Kernel>
void occupancy(Kernel k, int block, int& per_sm, int& regs) {
    struct Entry {
        const void* kernel;
        int block, per_sm, regs;
    };
    static std::vector<Entry> cache;
    const void* key = reinterpret_cast<const void*>(k);
    for (const Entry& e : cache)
        if (e.kernel == key && e.block == block) {
            per_sm = e.per_sm;
            regs = e.regs;
            return;
        }
    CUDA_CHECK(cudaOccupancyMaxActiveBlocksPerMultiprocessor(&per_sm, k, block, 0));
    cudaFuncAttributes a;
    CUDA_CHECK(cudaFuncGetAttributes(&a, k));
    regs = a.numRegs;
    cache.push_back({key, block, per_sm, regs});
}

McResult finish(const double h_acc[kAcc], long long n_units, long long n_paths, float kernel_ms,
                std::chrono::steady_clock::time_point t0) {
    McResult res;
    res.n_paths = n_paths;
    for (int k = 0; k < kOutputs; ++k) res.out[k] = estimate_from_sums(h_acc[k], h_acc[kOutputs + k], n_units);
    res.kernel_ms = kernel_ms;
    res.total_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    return res;
}

template <typename R, Model M, Product P, typename Opt>
McResult run(const McRequest& rq, const GpuConfig& cfg, GpuLaunchInfo* info) {
    const auto t0 = std::chrono::steady_clock::now();
    Context& cx = ctx();
    if (cfg.block > Opt::max_threads) throw std::invalid_argument("block too large for this variant");
    const PathConsts<R> c = make_consts<R>(rq);
    const long long n_units = rq.n_paths / Opt::legs;
    constexpr bool grouped = M == Model::BlackScholes && P == Product::European;
    const long long n_items = grouped ? (n_units + 3) / 4 : n_units;
    const double work_per_item = (grouped ? 4.0 : static_cast<double>(rq.product.n_steps)) * Opt::legs / 2;
    long long chunk = static_cast<long long>(cfg.max_work / work_per_item);
    if (chunk < 1) chunk = 1;
    if (!Opt::per_pair_rng) chunk = n_items;  // one state per thread: chunks would replay the same numbers

    int per_sm = 0, regs = 0;
    occupancy(mc_kernel<R, M, P, Opt>, cfg.block, per_sm, regs);
    const int grid = cfg.grid > 0 ? cfg.grid : cx.n_sm * per_sm;

    CUDA_CHECK(cudaMemsetAsync(cx.acc, 0, kAcc * sizeof(double)));
    CUDA_CHECK(cudaEventRecord(cx.e0));
    int launches = 0;
    for (long long b = 0; b < n_items; b += chunk, ++launches) {
        const long long e = b + chunk < n_items ? b + chunk : n_items;
        mc_kernel<R, M, P, Opt><<<grid, cfg.block>>>(c, rq.seed, b, e, n_units, cx.acc);
        CUDA_CHECK(cudaGetLastError());
    }
    CUDA_CHECK(cudaEventRecord(cx.e1));
    double h_acc[kAcc];
    CUDA_CHECK(cudaMemcpy(h_acc, cx.acc, sizeof(h_acc), cudaMemcpyDeviceToHost));  // synchronises
    float kernel_ms = 0.0f;
    CUDA_CHECK(cudaEventElapsedTime(&kernel_ms, cx.e0, cx.e1));
    if (info) *info = {cfg.block, grid, per_sm, launches, regs, 0.0};
    return finish(h_acc, n_units, rq.n_paths, kernel_ms, t0);
}

template <typename R, Model M, Product P>
McResult run_variant(const McRequest& rq, const GpuConfig& cfg, GpuLaunchInfo* info) {
    const GpuVariant& v = cfg.variant;
    const int n_set = v.fast_intrinsics + !v.antithetic + v.double_sums + v.normal1 + v.per_thread_rng + v.min_blocks4;
    if (n_set == 0) return run<R, M, P, Default>(rq, cfg, info);
    constexpr bool studied = std::is_same_v<R, float> &&
                             ((M == Model::BlackScholes && P != Product::Asian) ||
                              (M == Model::Heston && P == Product::Barrier));
    if constexpr (studied) {
        if (n_set > 1) throw std::invalid_argument("one variant switch at a time");
        if (v.fast_intrinsics) return run<R, M, P, KOpt<true>>(rq, cfg, info);
        if (!v.antithetic) return run<R, M, P, KOpt<false, false>>(rq, cfg, info);
        if (v.double_sums) return run<R, M, P, KOpt<false, true, false>>(rq, cfg, info);
        if (v.normal1) return run<R, M, P, KOpt<false, true, true, false>>(rq, cfg, info);
        if (v.per_thread_rng) return run<R, M, P, KOpt<false, true, true, true, false>>(rq, cfg, info);
        return run<R, M, P, KOpt<false, true, true, true, true, 4>>(rq, cfg, info);
    }
    throw std::invalid_argument("variants are compiled for float, BS European and the barriers only");
}

template <typename R>
McResult dispatch(const McRequest& rq, const GpuConfig& cfg, GpuLaunchInfo* info) {
    const Product p = rq.product.product;
    if (rq.model == Model::BlackScholes) {
        if (p == Product::European) return run_variant<R, Model::BlackScholes, Product::European>(rq, cfg, info);
        if (p == Product::Asian) return run_variant<R, Model::BlackScholes, Product::Asian>(rq, cfg, info);
        return run_variant<R, Model::BlackScholes, Product::Barrier>(rq, cfg, info);
    }
    if (p == Product::European) return run_variant<R, Model::Heston, Product::European>(rq, cfg, info);
    if (p == Product::Asian) return run_variant<R, Model::Heston, Product::Asian>(rq, cfg, info);
    return run_variant<R, Model::Heston, Product::Barrier>(rq, cfg, info);
}

template <Model M>
McResult run_stored(const McRequest& rq, const GpuConfig& cfg, GpuLaunchInfo* info) {
    const auto t0 = std::chrono::steady_clock::now();
    Context& cx = ctx();
    const PathConsts<float> c = make_consts<float>(rq);
    const long long n_pairs = rq.n_paths / 2;
    float* paths = nullptr;
    CUDA_CHECK(cudaMalloc(&paths, static_cast<size_t>(rq.n_paths) * rq.product.n_steps * sizeof(float)));
    int per_sm1 = 0, per_sm2 = 0, regs1 = 0, regs2 = 0;
    occupancy(store_kernel<M>, cfg.block, per_sm1, regs1);
    occupancy(stored_barrier_kernel, cfg.block, per_sm2, regs2);
    CUDA_CHECK(cudaMemsetAsync(cx.acc, 0, kAcc * sizeof(double)));
    CUDA_CHECK(cudaEventRecord(cx.e0));
    store_kernel<M><<<cx.n_sm * per_sm1, cfg.block>>>(c, rq.seed, n_pairs, paths);
    CUDA_CHECK(cudaGetLastError());
    CUDA_CHECK(cudaEventRecord(cx.e1));
    stored_barrier_kernel<<<cx.n_sm * per_sm2, cfg.block>>>(c, n_pairs, paths, cx.acc);
    CUDA_CHECK(cudaGetLastError());
    CUDA_CHECK(cudaEventRecord(cx.e2));
    double h_acc[kAcc];
    CUDA_CHECK(cudaMemcpy(h_acc, cx.acc, sizeof(h_acc), cudaMemcpyDeviceToHost));
    float ms1 = 0.0f, ms2 = 0.0f;
    CUDA_CHECK(cudaEventElapsedTime(&ms1, cx.e0, cx.e1));
    CUDA_CHECK(cudaEventElapsedTime(&ms2, cx.e1, cx.e2));
    CUDA_CHECK(cudaFree(paths));
    if (info) *info = {cfg.block, cx.n_sm * per_sm1, per_sm1, 2, regs1, ms2};
    return finish(h_acc, n_pairs, rq.n_paths, ms1 + ms2, t0);
}

}  // namespace

McResult gpu_price(const McRequest& rq, const GpuConfig& cfg, GpuLaunchInfo* info) {
    if (rq.n_paths < 4 || rq.n_paths % 2) throw std::invalid_argument("n_paths must be even and >= 4");
    if (cfg.block % 32 || cfg.block > kMaxBlock) throw std::invalid_argument("block must be a multiple of 32, <= 512");
    return cfg.use_double ? dispatch<double>(rq, cfg, info) : dispatch<float>(rq, cfg, info);
}

McResult gpu_price_stored(const McRequest& rq, const GpuConfig& cfg, GpuLaunchInfo* info) {
    if (rq.product.product != Product::Barrier) throw std::invalid_argument("stored paths: barrier products only");
    if (rq.n_paths < 4 || rq.n_paths % 2) throw std::invalid_argument("n_paths must be even and >= 4");
    return rq.model == Model::Heston ? run_stored<Model::Heston>(rq, cfg, info)
                                     : run_stored<Model::BlackScholes>(rq, cfg, info);
}

std::string gpu_device_name() {
    cudaDeviceProp p;
    CUDA_CHECK(cudaGetDeviceProperties(&p, 0));
    char buf[256];
    std::snprintf(buf, sizeof(buf), "%s (sm_%d%d, %d SM)", p.name, p.major, p.minor, p.multiProcessorCount);
    return buf;
}

}  // namespace optlab
