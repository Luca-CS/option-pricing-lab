/* C interface of the engine, exported by the optlab_cuda_c shared library and called from Python through ctypes
   (optlab/gpu.py). Plain C types only; the layouts below are mirrored there field by field. */
#ifndef OPTLAB_CUDA_CAPI_H
#define OPTLAB_CUDA_CAPI_H

#ifdef _WIN32
#define OPTLAB_API __declspec(dllexport)
#else
#define OPTLAB_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* model: 0 Black-Scholes, 1 Heston. product: 0 European, 1 Asian, 2 down-and-out barrier.
   legs: 0 none, 1 call, 2 put, 3 forward. Outputs as in params.hpp: European out[k] = leg k; Asian out[0]
   arithmetic, out[1] geometric; barrier out[0] down-and-out, out[1] same leg without barrier; out[3] = E[S_T]. */
typedef struct {
    int model, product;
    int legs[3];
    double strikes[3];
    double H;
    int n_steps;
    double S0, r, q, T;
    double sigma;
    double v0, kappa, theta, xi, rho;
    long long n_paths;
    unsigned long long seed;
} optlab_request;

typedef struct {
    double price[4], se[4];
    long long n_paths;
    double kernel_ms, total_ms;
} optlab_result;

/* Number of CUDA devices, 0 without device or driver. */
OPTLAB_API int optlab_gpu_count(void);

/* backend: 0 double CPU reference on n_threads threads (0: all), 1 GPU (float). Returns 0 on success; otherwise
   -1, and optlab_last_error() holds the message. Not thread-safe on the GPU backend. */
OPTLAB_API int optlab_price(const optlab_request* rq, int backend, int n_threads, optlab_result* out);

/* Message of the last failed call on this thread, or "" */
OPTLAB_API const char* optlab_last_error(void);

/* Device 0 as "name (sm_XY, N SM)", or "" without device */
OPTLAB_API const char* optlab_gpu_name(void);

#ifdef __cplusplus
}
#endif

#endif
