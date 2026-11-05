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

/* Paths for the Python library (paths.hpp): every model of optlab (constant, tabulated local variance, Heston, SLV),
   1 to 8 correlated names, an optional local correlation. Arrays in double, row major; out has n_names * n_cols
   columns of 2 n_pairs rows (pair p at row p, its antithetic at row n_pairs + p), column (a, c) at
   out + (a * n_cols + c) * 2 n_pairs. Draws depend on (seed, first_pair + p) only. */
typedef struct {
    int n_names, n_steps, n_obs, n_x, n_rows;
    long long n_pairs, first_pair;
    unsigned long long seed;
    const double* dt;        /* n_steps */
    const double* t0;        /* n_steps */
    const int* obs_step;     /* n_obs, nondecreasing, 0 = time 0 */
    const double* root;      /* n_names x n_names */
    const int* kind;         /* n_names: 0 constant, 1 table, 2 Heston, 3 SLV (table = squared leverage) */
    const double* cvar;      /* n_names */
    const double* heston;    /* n_names x 5: v0, kappa, theta, xi, rho */
    int same;                /* variance residuals correlated like the spots */
    const int* step_row;     /* n_names x n_steps, absolute row of the tables */
    const double* row_lo;    /* n_rows */
    const double* row_inv_dx;/* n_rows */
    const double* table;     /* n_rows x n_x */
    const double* fwd;       /* n_names x n_obs */
    int mixer;               /* 0 none, 1 local correlation */
    double mix[8];           /* w0, level, down, up, sigma, market sign, cos(turn), sin(turn) */
    const double* low_root;  /* n_names x n_names when mixer = 1 */
    int n_cols, n_keep;      /* columns per name in out; the first n_keep hold observations */
    const int* col_of;       /* n_obs: column of each observation, -1 for none */
    int n_windows;           /* at most 2: lowest and highest spot in columns n_keep + 2 w and n_keep + 2 w + 1 */
    const int* win_lo;       /* n_windows, first observation */
    const int* win_hi;       /* n_windows, last observation */
    double* out;
    double kernel_ms, total_ms;  /* filled on return */
} optlab_paths_request;

/* backend 0: CPU in double on n_threads threads (0: all); 1: GPU in float. Returns 0, or -1 and optlab_last_error(). */
OPTLAB_API int optlab_paths(optlab_paths_request* rq, int backend, int n_threads);

typedef struct {
    char name[256];
    int major, minor, sm_count, clock_mhz;
    long long total_mem, free_mem;
    int integrated, kernel_timeout;
} optlab_device;

/* 1 and the properties of a CUDA device, or 0 without it. */
OPTLAB_API int optlab_device_info(int device, optlab_device* out);

/* 1 when the library was built with CUDA, 0 for a CPU-only build. */
OPTLAB_API int optlab_has_cuda(void);

/* SLV leverage by the particle method (leverage.hpp), the algorithm of optlab.slv.calibrate_leverage. The local
   variance comes as uniform rows, one per step start. Fills x_grids and values (n_steps x n_x). */
typedef struct {
    int n_steps;
    const double* dt;
    const double* t0;
    long long n_pairs;
    unsigned long long seed;
    double v0, kappa, theta, xi, rho;  /* xi already scaled by the mixing fraction */
    int n_x, n_bins, n_fine;
    double atm_vol, bandwidth_scale, l_min, l_max, q_lo, q_hi;
    int local_linear, positive_part;
    int n_lv;
    const double* lv_lo;
    const double* lv_inv_dx;
    const double* lv_rows;
    double* x_grids;
    double* values;
    double total_ms;  /* filled on return */
} optlab_leverage_request;

/* backend 0: CPU (double) on n_threads threads (0: all); 1: GPU (float particles). 0 on success, -1 otherwise. */
OPTLAB_API int optlab_leverage(optlab_leverage_request* rq, int backend, int n_threads);

/* Hardware threads seen by the library. */
OPTLAB_API int optlab_cpu_threads(void);

#ifdef __cplusplus
}
#endif

#endif
