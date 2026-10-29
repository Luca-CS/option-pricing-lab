// Monte Carlo kernels. One thread prices one antithetic pair at a time (grid-stride loop), path state in float
// (or double for comparison), per-thread sums in double, warp-shuffle and shared-memory reduction, then one
// atomicAdd(double) per block and per accumulator.
#include "optlab_cuda/gpu_mc.cuh"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <stdexcept>

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

struct PhiloxNormals {
    curandStatePhilox4_32_10_t st;
    __device__ void next4(float z[4]) {
        const float4 n = curand_normal4(&st);
        z[0] = n.x, z[1] = n.y, z[2] = n.z, z[3] = n.w;
    }
    __device__ void next4(double z[4]) {  // same float normals, so float and double paths differ by arithmetic only
        const float4 n = curand_normal4(&st);
        z[0] = n.x, z[1] = n.y, z[2] = n.z, z[3] = n.w;
    }
};

template <typename R>
__device__ __forceinline__ void accumulate(const R out[kOutputs], double acc[kAcc]) {
    for (int k = 0; k < kOutputs; ++k) {
        const double x = static_cast<double>(out[k]);
        acc[k] += x;
        acc[kOutputs + k] += x * x;
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

// Work items [begin, end): one antithetic pair each, or four pairs for the Black-Scholes European (one
// curand_normal4 call serves four one-step pairs). Item i uses Philox subsequence i.
template <typename R, Model M, Product P>
__global__ void mc_kernel(PathConsts<R> c, unsigned long long seed, long long begin, long long end, long long n_pairs,
                          double* global_acc) {
    double acc[kAcc] = {};
    R out[kOutputs];
    const long long stride = static_cast<long long>(gridDim.x) * blockDim.x;
    for (long long it = begin + static_cast<long long>(blockIdx.x) * blockDim.x + threadIdx.x; it < end;
         it += stride) {
        PhiloxNormals src;
        curand_init(seed, static_cast<unsigned long long>(it), 0ull, &src.st);
        if constexpr (M == Model::BlackScholes && P == Product::European) {
            float z[4];
            src.next4(z);
            for (int j = 0; j < 4; ++j) {
                if (4 * it + j >= n_pairs) break;
                bs_european_pair(c, R(z[j]), out);
                accumulate(out, acc);
            }
        } else {
            path_pair<R, M, P>(c, src, out);
            accumulate(out, acc);
        }
    }
    block_reduce_add(acc, global_acc);
}

template <typename R, Model M, Product P>
McResult run(const McRequest& rq, const GpuConfig& cfg, GpuLaunchInfo* info) {
    const auto t0 = std::chrono::steady_clock::now();
    const PathConsts<R> c = make_consts<R>(rq);
    const long long n_pairs = rq.n_paths / 2;
    constexpr bool grouped = M == Model::BlackScholes && P == Product::European;
    const long long n_items = grouped ? (n_pairs + 3) / 4 : n_pairs;
    const double work_per_item = grouped ? 4.0 : static_cast<double>(rq.product.n_steps);
    const long long chunk = static_cast<long long>(cfg.max_work / work_per_item) > 0
                                ? static_cast<long long>(cfg.max_work / work_per_item)
                                : 1;

    int device = 0, n_sm = 0, per_sm = 0;
    CUDA_CHECK(cudaGetDevice(&device));
    CUDA_CHECK(cudaDeviceGetAttribute(&n_sm, cudaDevAttrMultiProcessorCount, device));
    CUDA_CHECK(cudaOccupancyMaxActiveBlocksPerMultiprocessor(&per_sm, mc_kernel<R, M, P>, cfg.block, 0));
    const int grid = cfg.grid > 0 ? cfg.grid : n_sm * per_sm;

    double* d_acc = nullptr;
    CUDA_CHECK(cudaMalloc(&d_acc, kAcc * sizeof(double)));
    CUDA_CHECK(cudaMemset(d_acc, 0, kAcc * sizeof(double)));
    cudaEvent_t e0, e1;
    CUDA_CHECK(cudaEventCreate(&e0));
    CUDA_CHECK(cudaEventCreate(&e1));
    CUDA_CHECK(cudaEventRecord(e0));
    int launches = 0;
    for (long long b = 0; b < n_items; b += chunk, ++launches) {
        const long long e = b + chunk < n_items ? b + chunk : n_items;
        mc_kernel<R, M, P><<<grid, cfg.block>>>(c, rq.seed, b, e, n_pairs, d_acc);
        CUDA_CHECK(cudaGetLastError());
    }
    CUDA_CHECK(cudaEventRecord(e1));
    CUDA_CHECK(cudaEventSynchronize(e1));
    float kernel_ms = 0.0f;
    CUDA_CHECK(cudaEventElapsedTime(&kernel_ms, e0, e1));
    double h_acc[kAcc];
    CUDA_CHECK(cudaMemcpy(h_acc, d_acc, sizeof(h_acc), cudaMemcpyDeviceToHost));
    CUDA_CHECK(cudaFree(d_acc));
    CUDA_CHECK(cudaEventDestroy(e0));
    CUDA_CHECK(cudaEventDestroy(e1));

    McResult res;
    res.n_paths = rq.n_paths;
    for (int k = 0; k < kOutputs; ++k) res.out[k] = estimate_from_sums(h_acc[k], h_acc[kOutputs + k], n_pairs);
    res.kernel_ms = kernel_ms;
    res.total_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    if (info) *info = {cfg.block, grid, per_sm, launches};
    return res;
}

template <typename R>
McResult dispatch(const McRequest& rq, const GpuConfig& cfg, GpuLaunchInfo* info) {
    const Product p = rq.product.product;
    if (rq.model == Model::BlackScholes) {
        if (p == Product::European) return run<R, Model::BlackScholes, Product::European>(rq, cfg, info);
        if (p == Product::Asian) return run<R, Model::BlackScholes, Product::Asian>(rq, cfg, info);
        return run<R, Model::BlackScholes, Product::Barrier>(rq, cfg, info);
    }
    if (p == Product::European) return run<R, Model::Heston, Product::European>(rq, cfg, info);
    if (p == Product::Asian) return run<R, Model::Heston, Product::Asian>(rq, cfg, info);
    return run<R, Model::Heston, Product::Barrier>(rq, cfg, info);
}

}  // namespace

McResult gpu_price(const McRequest& rq, const GpuConfig& cfg, GpuLaunchInfo* info) {
    if (rq.n_paths < 4 || rq.n_paths % 2) throw std::invalid_argument("n_paths must be even and >= 4");
    if (cfg.block % 32 || cfg.block > 1024) throw std::invalid_argument("block must be a multiple of 32, <= 1024");
    return cfg.use_double ? dispatch<double>(rq, cfg, info) : dispatch<float>(rq, cfg, info);
}

std::string gpu_device_name() {
    cudaDeviceProp p;
    CUDA_CHECK(cudaGetDeviceProperties(&p, 0));
    char buf[256];
    std::snprintf(buf, sizeof(buf), "%s (sm_%d%d, %d SM)", p.name, p.major, p.minor, p.multiProcessorCount);
    return buf;
}

}  // namespace optlab
