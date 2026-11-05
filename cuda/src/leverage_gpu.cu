// GPU device of the particle method (leverage.hpp): particles in float on the device, bins accumulated per block in
// shared memory and added to the device totals in double, one leverage row uploaded per step.
#include <cuda_runtime.h>

#include <cfloat>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <vector>

#include "optlab_cuda/leverage.hpp"
#include "optlab_cuda/philox.hpp"

namespace optlab {

namespace {

void check(cudaError_t e, const char* what) {
    if (e != cudaSuccess) {
        char buf[512];
        std::snprintf(buf, sizeof buf, "CUDA error in %s: %s", what, cudaGetErrorString(e));
        throw std::runtime_error(buf);
    }
}

// Order-preserving map of floats to ints, for atomicMin / atomicMax on floats.
__device__ __forceinline__ int ordered(float f) {
    const int i = __float_as_int(f);
    return i >= 0 ? i : i ^ 0x7FFFFFFF;
}
float unordered(int i) {
    const int j = i >= 0 ? i : i ^ 0x7FFFFFFF;
    float f;
    std::memcpy(&f, &j, sizeof f);
    return f;
}

constexpr int kMaxBins = 512;

__global__ void bin_kernel(const float* __restrict__ x, const float* __restrict__ v, long long n, float lo, float hi,
                           int n_fine, int nb, int positive, unsigned* fine, double* acc, double* v_sum) {
    __shared__ float sh[5 * kMaxBins + 1];
    for (int k = threadIdx.x; k < 5 * nb + 1; k += blockDim.x) sh[k] = 0.0f;
    __syncthreads();
    const float wf = (hi - lo) / n_fine, we = (hi + 1e-12f - lo) / nb;
    for (long long j = blockIdx.x * static_cast<long long>(blockDim.x) + threadIdx.x; j < n;
         j += static_cast<long long>(gridDim.x) * blockDim.x) {
        const float xj = x[j], vj = positive ? fmaxf(v[j], 0.0f) : v[j];
        int f = wf > 0 ? static_cast<int>((xj - lo) / wf) : 0;
        atomicAdd(&fine[min(max(f, 0), n_fine - 1)], 1u);
        int k = min(max(static_cast<int>((xj - lo) / we), 0), nb - 1);
        atomicAdd(&sh[k], 1.0f);
        atomicAdd(&sh[nb + k], vj);
        atomicAdd(&sh[2 * nb + k], xj);
        atomicAdd(&sh[3 * nb + k], xj * xj);
        atomicAdd(&sh[4 * nb + k], xj * vj);
        atomicAdd(&sh[5 * nb], vj);
    }
    __syncthreads();
    for (int k = threadIdx.x; k < 5 * nb; k += blockDim.x)
        if (sh[k] != 0.0f) atomicAdd(&acc[k], static_cast<double>(sh[k]));
    if (threadIdx.x == 0) atomicAdd(v_sum, static_cast<double>(sh[5 * nb]));
}

__global__ void step_kernel(float* x, float* v, long long n_pairs, int step, std::uint32_t k0, std::uint32_t k1, float h,
                            float a, float b, const float* __restrict__ lev, int nx, float rho, float kappa, float theta,
                            float xi, int* range) {
    const long long p = blockIdx.x * static_cast<long long>(blockDim.x) + threadIdx.x;
    float mn = FLT_MAX, mx = -FLT_MAX;
    if (p < n_pairs) {
        float z[4];
        normals4<float>(static_cast<std::uint64_t>(p), static_cast<std::uint32_t>(step + 1), 0u, k0, k1, z);
        const float rc = sqrtf(1.0f - rho * rho), scale = (nx - 1) / (b - a);
        for (int leg = 0; leg < 2; ++leg) {
            const long long j = leg ? n_pairs + p : p;
            const float sg = leg ? -1.0f : 1.0f, z1 = sg * z[0], z2 = sg * z[1];
            float u = (x[j] - a) * scale, L;
            if (!(u > 0.0f)) L = lev[0];
            else if (u >= nx - 1) L = lev[nx - 1];
            else {
                const int i = static_cast<int>(u);
                const float w = u - i;
                L = lev[i] * (1.0f - w) + lev[i + 1] * w;
            }
            const float vp = fmaxf(v[j], 0.0f), var = L * L * vp;
            v[j] += kappa * (theta - vp) * h + xi * sqrtf(vp * h) * z2;
            x[j] += -0.5f * var * h + sqrtf(var * h) * (rho * z2 + rc * z1);
            mn = fminf(mn, x[j]), mx = fmaxf(mx, x[j]);
        }
    }
    // block min and max, then one atomic each
    __shared__ float smin[256], smax[256];
    smin[threadIdx.x] = mn, smax[threadIdx.x] = mx;
    __syncthreads();
    for (int s = blockDim.x / 2; s > 0; s >>= 1) {
        if (threadIdx.x < s) {
            smin[threadIdx.x] = fminf(smin[threadIdx.x], smin[threadIdx.x + s]);
            smax[threadIdx.x] = fmaxf(smax[threadIdx.x], smax[threadIdx.x + s]);
        }
        __syncthreads();
    }
    if (threadIdx.x == 0) {
        atomicMin(&range[0], ordered(smin[0]));
        atomicMax(&range[1], ordered(smax[0]));
    }
}

class GpuParticles final : public ParticleDevice {
public:
    explicit GpuParticles(const LeverageSpec& s) : s_(s), n_(2 * s.n_pairs) {
        if (s.n_bins > kMaxBins) throw std::invalid_argument("n_bins must be <= 512 on the GPU");
        check(cudaMalloc(&x_, n_ * sizeof(float)), "cudaMalloc");
        check(cudaMalloc(&v_, n_ * sizeof(float)), "cudaMalloc");
        check(cudaMalloc(&fine_, s.n_fine * sizeof(unsigned)), "cudaMalloc");
        check(cudaMalloc(&acc_, (5 * s.n_bins + 1) * sizeof(double)), "cudaMalloc");
        check(cudaMalloc(&lev_, s.n_x * sizeof(float)), "cudaMalloc");
        check(cudaMalloc(&range_, 2 * sizeof(int)), "cudaMalloc");
        check(cudaMemset(x_, 0, n_ * sizeof(float)), "cudaMemset");
        std::vector<float> v0(static_cast<size_t>(n_), static_cast<float>(s.v0));
        check(cudaMemcpy(v_, v0.data(), n_ * sizeof(float), cudaMemcpyHostToDevice), "cudaMemcpy");
        fine_h_.resize(s.n_fine);
        acc_h_.resize(5 * s.n_bins + 1);
        int dev = 0, sms = 1;
        cudaGetDevice(&dev);
        cudaDeviceGetAttribute(&sms, cudaDevAttrMultiProcessorCount, dev);
        bin_blocks_ = 4 * sms;
    }
    ~GpuParticles() override {
        cudaFree(x_), cudaFree(v_), cudaFree(fine_), cudaFree(acc_), cudaFree(lev_), cudaFree(range_);
    }

    void bin(double lo, double hi, Bins& out) override {
        const int nb = s_.n_bins, nf = s_.n_fine;
        check(cudaMemset(fine_, 0, nf * sizeof(unsigned)), "cudaMemset");
        check(cudaMemset(acc_, 0, (5 * nb + 1) * sizeof(double)), "cudaMemset");
        bin_kernel<<<bin_blocks_, 256>>>(x_, v_, n_, static_cast<float>(lo), static_cast<float>(hi), nf, nb,
                                         s_.positive_part, fine_, acc_, acc_ + 5 * nb);
        check(cudaGetLastError(), "bin_kernel");
        check(cudaMemcpy(fine_h_.data(), fine_, nf * sizeof(unsigned), cudaMemcpyDeviceToHost), "cudaMemcpy");
        check(cudaMemcpy(acc_h_.data(), acc_, (5 * nb + 1) * sizeof(double), cudaMemcpyDeviceToHost), "cudaMemcpy");
        for (int k = 0; k < nf; ++k) out.fine[k] = fine_h_[k];
        for (int k = 0; k < 5 * nb; ++k) out.acc[k] = acc_h_[k];
        out.v_sum = acc_h_[5 * nb];
    }

    void step(int i, double h, double a, double b, const std::vector<double>& lev, double& lo, double& hi) override {
        std::vector<float> lf(lev.begin(), lev.end());
        check(cudaMemcpy(lev_, lf.data(), lf.size() * sizeof(float), cudaMemcpyHostToDevice), "cudaMemcpy");
        const int init[2] = {0x7FFFFFFF, static_cast<int>(0x80000000u)};
        check(cudaMemcpy(range_, init, sizeof init, cudaMemcpyHostToDevice), "cudaMemcpy");
        const int block = 256;
        const unsigned grid = static_cast<unsigned>((s_.n_pairs + block - 1) / block);
        step_kernel<<<grid, block>>>(x_, v_, s_.n_pairs, i, static_cast<std::uint32_t>(s_.seed),
                                     static_cast<std::uint32_t>(s_.seed >> 32), static_cast<float>(h),
                                     static_cast<float>(a), static_cast<float>(b), lev_, s_.n_x,
                                     static_cast<float>(s_.rho), static_cast<float>(s_.kappa),
                                     static_cast<float>(s_.theta), static_cast<float>(s_.xi), range_);
        check(cudaGetLastError(), "step_kernel");
        int r[2];
        check(cudaMemcpy(r, range_, sizeof r, cudaMemcpyDeviceToHost), "cudaMemcpy");
        lo = unordered(r[0]), hi = unordered(r[1]);
    }

private:
    const LeverageSpec& s_;
    long long n_;
    float *x_ = nullptr, *v_ = nullptr, *lev_ = nullptr;
    unsigned* fine_ = nullptr;
    double* acc_ = nullptr;
    int* range_ = nullptr;
    int bin_blocks_ = 160;
    std::vector<unsigned> fine_h_;
    std::vector<double> acc_h_;
};

}  // namespace

double gpu_leverage(const LeverageSpec& s) {
    GpuParticles dev(s);
    return calibrate_leverage(s, dev);
}

}  // namespace optlab
