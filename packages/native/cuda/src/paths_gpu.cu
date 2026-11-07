// Path engine on the GPU: one thread per antithetic pair, float arithmetic, double output. The specification is
// converted to float and copied once per call; the pairs run in chunks on two streams, so the copy of one chunk
// back to the host overlaps the kernel of the next, and no launch lasts long enough for the display watchdog.
#include <cuda_runtime.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <vector>

#include "optlab_cuda/paths_engine.hpp"

namespace optlab {

namespace {

void check(cudaError_t e, const char* what) {
    if (e != cudaSuccess) {
        char buf[512];
        std::snprintf(buf, sizeof buf, "CUDA error in %s: %s", what, cudaGetErrorString(e));
        throw std::runtime_error(buf);
    }
}

template <int N>
__global__ void __launch_bounds__(256) paths_kernel(PathSpec<float> s) {
    const long long p = static_cast<long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (p < s.n_pairs) simulate_pair<float, N>(s, p);
}

// Device copies of the specification's arrays, freed on scope exit.
struct DeviceArrays {
    std::vector<void*> ptrs;
    ~DeviceArrays() {
        for (void* p : ptrs) cudaFree(p);
    }
    template <typename T>
    T* put(const std::vector<T>& host) {
        if (host.empty()) return nullptr;
        void* d = nullptr;
        check(cudaMalloc(&d, host.size() * sizeof(T)), "cudaMalloc");
        ptrs.push_back(d);
        check(cudaMemcpy(d, host.data(), host.size() * sizeof(T), cudaMemcpyHostToDevice), "cudaMemcpy");
        return static_cast<T*>(d);
    }
    void* raw(size_t bytes) {
        void* d = nullptr;
        check(cudaMalloc(&d, bytes), "cudaMalloc");
        ptrs.push_back(d);
        return d;
    }
};

std::vector<float> to_float(const double* p, long long n) {
    std::vector<float> out(p ? static_cast<size_t>(n) : 0);
    for (long long i = 0; p && i < n; ++i) out[i] = static_cast<float>(p[i]);
    return out;
}

std::vector<int> ints(const int* p, long long n) { return p ? std::vector<int>(p, p + n) : std::vector<int>(); }

void launch(const PathSpec<float>& s, cudaStream_t st) {
    const int block = 256;
    const unsigned grid = static_cast<unsigned>((s.n_pairs + block - 1) / block);
    if (s.n_names <= 1) paths_kernel<1><<<grid, block, 0, st>>>(s);
    else if (s.n_names <= 2) paths_kernel<2><<<grid, block, 0, st>>>(s);
    else if (s.n_names <= 4) paths_kernel<4><<<grid, block, 0, st>>>(s);
    else paths_kernel<kMaxNames><<<grid, block, 0, st>>>(s);
    check(cudaGetLastError(), "kernel launch");
}

}  // namespace

PathTiming gpu_paths(const PathSpec<double>& h, int n_rows) {
    const auto t_start = std::chrono::steady_clock::now();
    const int n = h.n_names;
    DeviceArrays dev;
    PathSpec<float> s;
    s.n_names = n, s.n_steps = h.n_steps, s.n_obs = h.n_obs, s.n_x = h.n_x;
    s.key0 = h.key0, s.key1 = h.key1, s.same = h.same, s.mixer = h.mixer;
    for (int k = 0; k < 8; ++k) s.mix[k] = static_cast<float>(h.mix[k]);
    s.dt = dev.put(to_float(h.dt, h.n_steps));
    s.t0 = dev.put(to_float(h.t0, h.n_steps));
    s.obs_step = dev.put(ints(h.obs_step, h.n_obs));
    s.root = dev.put(to_float(h.root, n * n));
    s.kind = dev.put(ints(h.kind, n));
    s.cvar = dev.put(to_float(h.cvar, n));
    s.hes = dev.put(to_float(h.hes, 5 * n));
    s.step_row = dev.put(ints(h.step_row, static_cast<long long>(n) * h.n_steps));
    s.lo = dev.put(to_float(h.lo, n_rows));
    s.inv_dx = dev.put(to_float(h.inv_dx, n_rows));
    s.table = dev.put(to_float(h.table, static_cast<long long>(n_rows) * h.n_x));
    s.fwd = dev.put(to_float(h.fwd, static_cast<long long>(n) * h.n_obs));
    s.low_root = dev.put(to_float(h.low_root, h.mixer ? n * n : 0));
    s.n_cols = h.n_cols, s.n_keep = h.n_keep, s.n_win = h.n_win;
    s.col_of = dev.put(ints(h.col_of, h.n_obs));
    s.win_lo = dev.put(ints(h.win_lo, h.n_win));
    s.win_hi = dev.put(ints(h.win_hi, h.n_win));

    // Chunk size: about 2^27 pair-steps-names per launch (tens of ms), and two output buffers within a quarter of the
    // free memory (at most 1 GB each).
    const long long cols = static_cast<long long>(n) * h.n_cols;
    size_t free_mem = 0, total_mem = 0;
    check(cudaMemGetInfo(&free_mem, &total_mem), "cudaMemGetInfo");
    const long long work = std::max<long long>(1, static_cast<long long>(h.n_steps) * n);
    long long chunk = std::max<long long>(4096, (1LL << 27) / work);
    const long long budget = std::min<long long>(static_cast<long long>(free_mem) / 8, 1LL << 30);
    if (cols > 0) chunk = std::min(chunk, std::max<long long>(1024, budget / (cols * 2 * 8)));
    chunk = std::min(chunk, h.n_pairs);

    double* dout[2] = {nullptr, nullptr};
    cudaStream_t streams[2];
    cudaEvent_t ev0, ev1;
    for (int b = 0; b < 2; ++b) {
        dout[b] = static_cast<double*>(dev.raw(std::max<long long>(1, cols * 2 * chunk) * sizeof(double)));
        check(cudaStreamCreate(&streams[b]), "cudaStreamCreate");
    }
    check(cudaEventCreate(&ev0), "cudaEventCreate");
    check(cudaEventCreate(&ev1), "cudaEventCreate");
    const size_t host_bytes = static_cast<size_t>(cols * h.rows) * sizeof(double);
    const bool pinned = host_bytes >= (8u << 20) && cudaHostRegister(h.out, host_bytes, cudaHostRegisterDefault) == cudaSuccess;
    if (!pinned) cudaGetLastError();  // clear a failed registration: the copies then go through pageable memory

    PathTiming timing;
    try {
        check(cudaEventRecord(ev0, streams[0]), "cudaEventRecord");
        int b = 0;
        for (long long c0 = 0; c0 < h.n_pairs; c0 += chunk, b ^= 1) {
            const long long m = std::min(chunk, h.n_pairs - c0);
            PathSpec<float> part = s;
            part.n_pairs = m, part.first_pair = h.first_pair + c0, part.rows = 2 * m, part.row0 = 0, part.out_pairs = m;
            part.out = dout[b];
            launch(part, streams[b]);
            if (cols > 0) {
                // leg 0 of the chunk to host rows row0 + c0, leg 1 to out_pairs + row0 + c0
                const size_t width = static_cast<size_t>(m) * sizeof(double);
                const size_t spitch = static_cast<size_t>(2 * m) * sizeof(double);
                const size_t dpitch = static_cast<size_t>(h.rows) * sizeof(double);
                double* host0 = h.out + h.row0 + c0;
                double* host1 = h.out + h.out_pairs + h.row0 + c0;
                check(cudaMemcpy2DAsync(host0, dpitch, dout[b], spitch, width, cols, cudaMemcpyDeviceToHost, streams[b]),
                      "cudaMemcpy2DAsync");
                check(cudaMemcpy2DAsync(host1, dpitch, dout[b] + m, spitch, width, cols, cudaMemcpyDeviceToHost,
                                        streams[b]), "cudaMemcpy2DAsync");
            }
        }
        check(cudaStreamSynchronize(streams[0]), "cudaStreamSynchronize");
        check(cudaStreamSynchronize(streams[1]), "cudaStreamSynchronize");
        check(cudaEventRecord(ev1, streams[0]), "cudaEventRecord");
        check(cudaEventSynchronize(ev1), "cudaEventSynchronize");
        float ms = 0.0f;
        check(cudaEventElapsedTime(&ms, ev0, ev1), "cudaEventElapsedTime");
        timing.kernel_ms = ms;
    } catch (...) {
        if (pinned) cudaHostUnregister(h.out);
        for (auto& st : streams) cudaStreamDestroy(st);
        cudaEventDestroy(ev0), cudaEventDestroy(ev1);
        throw;
    }
    if (pinned) cudaHostUnregister(h.out);
    for (auto& st : streams) cudaStreamDestroy(st);
    cudaEventDestroy(ev0), cudaEventDestroy(ev1);
    timing.total_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t_start).count();
    return timing;
}

bool gpu_device_info(int device, DeviceInfo& info) {
    int count = 0;
    if (cudaGetDeviceCount(&count) != cudaSuccess || device < 0 || device >= count) {
        cudaGetLastError();
        return false;
    }
    cudaDeviceProp p{};
    if (cudaGetDeviceProperties(&p, device) != cudaSuccess) return false;
    std::snprintf(info.name, sizeof info.name, "%s", p.name);
    info.major = p.major, info.minor = p.minor, info.sm_count = p.multiProcessorCount;
    int khz = 0;
    cudaDeviceGetAttribute(&khz, cudaDevAttrClockRate, device);
    info.clock_mhz = khz / 1000;
    info.total_mem = static_cast<long long>(p.totalGlobalMem);
    info.integrated = p.integrated;
    int timeout = 0;
    cudaDeviceGetAttribute(&timeout, cudaDevAttrKernelExecTimeout, device);
    info.kernel_timeout = timeout;
    size_t fr = 0, tot = 0;
    int prev = 0;
    cudaGetDevice(&prev);
    if (cudaSetDevice(device) == cudaSuccess && cudaMemGetInfo(&fr, &tot) == cudaSuccess) info.free_mem = static_cast<long long>(fr);
    cudaSetDevice(prev);
    cudaGetLastError();
    return true;
}

}  // namespace optlab
