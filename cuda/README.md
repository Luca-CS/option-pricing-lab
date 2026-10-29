# GPU Monte Carlo

A C++/CUDA Monte Carlo engine for the products of `optlab` under Black-Scholes and Heston, with a double-precision
CPU reference that runs the same path code. Every GPU price is checked against an independent reference within
4 standard errors. Every speed-up is measured, and every optimisation is kept only if it leaves the price unchanged.

**Hardware**: NVIDIA GeForce RTX 2070 Super (Turing TU104, compute capability 7.5 / `sm_75`, 40 SMs, 8 GB GDDR6 at
448 GB/s, FP64 at 1/32 of the FP32 rate), driver 617.42, CUDA 12.9. CPU: AMD Ryzen 5 3600 (6 cores, 12 threads).
Windows 11, MSVC 19.44.

## Results

**Speed.** Heston with Andersen's QE scheme, down-and-out call, 10^7 paths × 252 daily steps: **91 ms** on the
GPU, against 15.1 s for the C++ reference on all 12 CPU threads (**×165**) and 121 s on one thread. That is
2.8×10^10 path-steps per second.

| Product | N | Steps | CPU C++, 1 thread (s) | CPU C++, 12 threads (s) | NumPy, `optlab` (s) | GPU kernel (ms), median [min, max] | GPU end to end (ms) | Speed-up vs 1 thread | Speed-up vs 12 threads | GPU path-steps/s |
|---|---|---|---|---|---|---|---|---|---|---|
| BS European | 10^6 | 1 | 0.022 | 0.004 | 0.04 | 0.033 [0.033, 0.033] | 0.07 | 686 | 111 | 3.0e10 |
| BS European | 10^7 | 1 | 0.22 | 0.027 | 0.43 | 0.11 [0.11, 0.11] | 0.14 | 1 983 | 245 | 9.0e10 |
| BS European | 10^8 | 1 | 2.20 | 0.267 | 4.15 | 0.98 [0.97, 0.98] | 1.01 | 2 250 | 273 | 1.0e11 |
| BS down-and-out | 10^6 | 252 | 2.30 | 0.298 | 9.26* | 0.84 [0.84, 0.84] | 0.87 | 2 747 | 356 | 3.0e11 |
| BS down-and-out | 10^7 | 252 | 22.8* | 2.96 | 91.3* | 8.52 [8.52, 8.52] | 8.58 | 2 669 | 348 | 3.0e11 |
| Heston European | 10^5 | 252 | 1.18 | 0.148 | 0.53 | 1.09 [1.09, 1.09] | 1.12 | 1 089 | 136 | 2.3e10 |
| Heston European | 10^6 | 252 | 11.9* | 1.49 | 16.6* | 9.26 [9.25, 9.26] | 9.32 | 1 280 | 161 | 2.7e10 |
| Heston European | 10^7 | 252 | 118.6* | 14.8* | 166* | 92.2 [91.9, 92.8] | 92.3 | 1 287 | 161 | 2.7e10 |
| Heston down-and-out | 10^5 | 252 | 1.21 | 0.151 | 0.81 | 1.08 [1.08, 1.08] | 1.11 | 1 123 | 140 | 2.3e10 |
| Heston down-and-out | 10^6 | 252 | 12.1* | 1.51 | 20.0* | 9.22 [9.19, 9.22] | 9.26 | 1 313 | 163 | 2.7e10 |
| Heston down-and-out | 10^7 | 252 | 121.3* | 15.1* | 200* | 91.2 [90.8, 92.9] | 91.2 | 1 331 | 165 | 2.8e10 |

`*`: measured at a smaller N, then extrapolated linearly. For NumPy this is optimistic, since its throughput
falls as N grows (the library stores every path). The first call costs about 100 ms, for context creation and
module loading; every later call adds 0.03 to 0.07 ms to the kernel time. For the European, the last column is
paths per second.

How to read these numbers:
- The CPU reference is the one the engine is validated against: scalar C++, `std::mt19937_64` with
  `std::normal_distribution`, one thread per slice of paths. It is not an optimised CPU engine. Vectorised normals
  (MKL, AVX2) would narrow the gap, but a Box-Muller generator was 7 to 13 % *slower* than
  `std::normal_distribution`, so the generator alone is not what holds the CPU back.
- The NumPy column does not run the same algorithm. `optlab` simulates Heston with full-truncation Euler on
  325 graded steps. It is shown as the cost of the existing Python library, not as a like-for-like comparison.

**Validation.** GPU in float: 10^7 paths for Black-Scholes, 4×10^6 paths × 252 steps for Heston. Seed 20261029.
Heston parameters calibrated on SPX (v0 0.0228, kappa 3.09, theta 0.0537, xi 1.31, rho -0.688; Feller ratio 0.19).
S0 = K = 100, T = 1, r = 4 %, q = 1 %, barrier H = 80 monitored daily. Every row passes, and the same table passes
on the CPU reference.

| Case | GPU price | SE | Reference | Gap (SE) |
|---|---|---|---|---|
| BS call | 9.322552 | 0.003294 | 9.319738 (closed form) | +0.85 |
| BS put | 6.395188 | 0.002117 | 6.393699 (closed form) | +0.70 |
| Call - put, same paths | 2.927364 | 0.001254 | 2.926039 (parity) | +1.06 |
| BS E[S_T] | 103.046832 | 0.001305 | 103.045453 | +1.06 |
| BS arithmetic Asian call, 12 fixings | 5.574661 | 0.001882 | 5.575692 ± 0.000114 (Python MC, control variate) | -0.55 |
| BS geometric Asian call | 5.380313 | 0.001833 | 5.381250 (closed form) | -0.51 |
| BS down-and-out call | 9.241401 | 0.003318 | 9.245769 ± 0.007422 (Python MC) | -0.54 |
| BS down-and-out call (indicative) | 9.241401 | 0.003318 | 9.243397 (Reiner-Rubinstein + BGK shift) | -0.60 |
| Heston call K 100 | 8.397952 | 0.004121 | 8.403656 (Fourier) | -1.38 |
| Heston call K 119 | 1.006351 | 0.001915 | 1.010290 (Fourier) | -2.06 |
| Heston put K 80 | 1.741639 | 0.003351 | 1.737996 (Fourier) | +1.09 |
| Heston E[S_T] | 103.033746 | 0.008278 | 103.045453 | -1.41 |
| Heston down-and-out call | 8.083289 | 0.004123 | 8.0927 ± 0.0092 (QE in the demo's JavaScript) | -0.93 |

The Heston vanillas are allowed a discretisation bias of 0.02 against Fourier, but none is needed. At 10^8 paths
(1 s on the GPU), the daily QE bias is not detectable at the 1e-3 level: call 100 -0.0001 ± 0.0008, call 119
+0.0006 ± 0.0004, put 80 +0.0012 ± 0.0007. Float and double path states, run on the same normals, agree within
**0.0055 standard errors** on every Heston output.

**Optimisation study.** One change at a time against the production kernel, with the same normals wherever the
change allows. Heston down-and-out at 10^7 paths, BS European at 10^8. The price gap is in standard errors and is
pure arithmetic when the normals are shared. Efficiency is 1 / (time × SE²), i.e. accuracy per unit of time.

| Variant | Heston (ms) | Price gap (SE) | BS European (ms) | Price gap (SE) | Verdict |
|---|---|---|---|---|---|
| **Production** (float, see below) | **90.4** | – | **0.985** | – | |
| Path state in double | 5 259 (×58) | -0.009 | 21.6 (×22) | -0.0002 | FP64 runs at 1/32 rate, and the gain is invisible |
| IEEE division and square root (nvcc default) | 169.1 (×1.87) | +0.001 | 1.04 | 0 | approximate versions adopted |
| `__expf` / `__logf` intrinsics | 74.5 (÷1.21) | **-0.135** | 0.874 | 0.000 | rejected: biased, and the bias outgrows the noise beyond 10^9 paths |
| `--use_fast_math` | 60.3 (÷1.50) | **-0.135** | 0.791 | 0 | rejected, same bias |
| Per-thread sums in double instead of Kahan float | 91.7 | 0.000 | 5.89 (×6.0) | 0.000 | Kahan float adopted |
| Blocks of 256 / 128 instead of 512 | 95.0 / 96.6 | 0.000 | 1.03 / 1.05 | 0.000 | 512 adopted |
| `__launch_bounds__(256, 4)` | 95.3 | 0.000 | 1.04 | 0.000 | no effect (already 59 to 62 registers, 100 % occupancy) |
| `curand_normal` ×4 instead of `curand_normal4` | 90.6 | 0 | 0.985 | 0 | equal |
| One Philox state per thread instead of per pair | 94.4 | – | 1.02 | – | per pair kept: free, and prices independent of the launch |
| No antithetic (same N) | 104.9, efficiency ÷1.44 | – | 1.51, efficiency ÷2.75 | – | antithetic kept |

Stored paths against the fused kernel (2×10^6 paths × 252 steps, 2 GB of float paths written then read): for
Heston, 24.2 ms against 18.6 ms (×1.30), since the work is dominated by arithmetic. For the BS barrier, 13.8 ms
against 1.70 ms (**×8.1**): the stored version moves 4 GB at 293 GB/s and becomes memory-bound (card peak
448 GB/s).

**Profile (Nsight Compute).** All three kernels are compute-bound: 75 to 80 % of the SM throughput, memory below
3 % of its peak, no register spills. The BS kernels saturate the FMA and ALU pipes (`math_pipe_throttle` is the
leading stall), so the only faster version would compute less, and the precise `expf` is the part that could be
cut. The Heston kernel showed **16 active threads per warp out of 32**. Most warps mix the quadratic and
exponential branches of QE, and each branch had its own logarithm for the martingale correction, plus two normal
cdfs on the exponential side. Taking that logarithm once after the branch, and testing U ≤ p as 1 − U ≥ 1 − p
(one cdf), cut the Heston kernel from 130.4 to 90.4 ms (×1.44) with the price unchanged to six decimals.

## Method

- **Schemes.** Black-Scholes is simulated with exact steps in the log-return y = ln(S/S0). Heston uses Andersen's
  QE-M scheme (2008), with the martingale correction, so that E[S_T] = S0 e^{(r-q)T} holds per step. In the
  exponential branch, 1 − U is computed as Φ(−Z_v), exact in both tails, which removes the usual `1 − 1e-7` clamp.
  Products: European (call, put, forward legs on the same paths), Asian with 12 fixings (arithmetic and geometric
  on the same paths), and down-and-out call monitored daily. The barrier loop runs to maturity with a flag, so
  knocked-out paths do not add divergence.
- **One source for CPU and GPU.** `include/optlab_cuda/schemes.hpp` holds the steps and payoffs as
  `__host__ __device__` templates on the real type. The CPU reference (double, `std::thread`) and the GPU kernels
  (float, or double for the study) run the same algorithm by construction. Path math has no double literals,
  because a stray `0.5` would promote a float expression to the 1/32-rate FP64 pipe.
- **Kernel.** One thread handles one antithetic pair at a time, in a grid-stride loop. The grid is
  SMs × resident blocks (from the occupancy API), with blocks of 512. Random numbers are cuRAND Philox-4x32-10
  through `curand_normal4`; pair p uses subsequence p of the seed. A price therefore depends only on (seed, N),
  not on the grid, the block size or the number of launches.
- **Accumulation.** Each thread keeps compensated (Kahan) float sums of the pair averages and of their squares.
  Blocks reduce them with warp shuffles, then shared memory, then one `atomicAdd(double)` per block. The
  standard error is computed on pair averages, so it accounts for the antithetic correlation.
- **Launches.** Work is split into launches of at most 2.5×10^8 path-steps, which keeps each launch far below
  the 2 s Windows watchdog (TDR). Accumulators and events are cached across calls.
- **Validation criterion.** |price − reference| ≤ 4 √(SE² + SE_ref²), plus 0.02 for Heston against Fourier
  (QE discretisation). The Heston barrier is checked against an independent QE implementation (the JavaScript of
  the demo page), not against the Euler price of the Python library, which differs by a scheme effect.
- **Benchmark protocol.** The GPU is timed only after a sustained warm-up of 2 s, then at least 300 ms per row:
  a single warm-up call after seconds of CPU work leaves the GPU at its 300 MHz idle clock. Each timing is the
  median [min, max] of 5 repetitions. The kernel time comes from CUDA events summed over launches, the end-to-end
  time from `steady_clock`. During the runs the GPU stayed at 1 860 to 1 980 MHz and at most 72 °C.

## Limitations

- **Two models, three products.** No local or stochastic-local volatility, no American exercise
  (Longstaff-Schwartz needs the stored paths and a regression, which is a different kernel shape), no Greeks.
  Parameters are flat: constant rate, dividend yield and Heston parameters.
- **Float paths.** The path state is single precision. The study bounds its effect at 0.009 standard errors at
  10^7 paths, but the bound is relative to the noise: a run of 10^10 paths would need re-checking.
- **One card.** Every number comes from one RTX 2070 Super under Windows. The trade-offs that depend on the card
  (FP64 rate, best block size, memory-bound against compute-bound) would move on a data-centre GPU with full-rate
  FP64.
- **The CPU baseline is a reference, not a competitor** (see Results).
- **Daily barrier only.** Continuous monitoring would go through the Broadie-Glasserman-Kou shift, as in `optlab`.

## Running

Requires CUDA 12.x, CMake ≥ 3.24 and a C++17 compiler. On Windows: Visual Studio 2022 Build Tools, with
`CUDA_PATH` set. The architecture is fixed to `sm_75` in `CMakeLists.txt`.

```bash
cmake -S cuda -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release
ctest --test-dir build -C Release                     # validation table (CPU and GPU) and the 10^8-path QE bias test
build/Release/test_prices.exe gpu                     # one suite: cpu | gpu | double | closed | qe-bias
python cuda/bench/bench_numpy.py                      # NumPy timings of optlab (venv), read by bench.exe
build/Release/bench.exe                               # benchmark table above
build/Release/ablation.exe                            # optimisation study (also ablation_ieee, ablation_fastmath)
```

To profile, run `ncu --set full -k regex:mc_kernel -o build/ncu_profile build/Release/profile.exe` from an
administrator shell, unless GPU counters are open to all users.

```cpp
#include "optlab_cuda/gpu_mc.cuh"

optlab::McRequest rq;                               // S0 100, r 4 %, q 1 %, T 1, seed 20261029
rq.model = optlab::Model::Heston;                   // SPX-calibrated parameters by default
rq.product.product = optlab::Product::Barrier;      // down-and-out call, K 100, H 80, 252 steps
rq.product.legs = {optlab::Leg::Call, optlab::Leg::None, optlab::Leg::None};
rq.product.strikes = {100, 100, 100};
rq.product.H = 80;
rq.product.n_steps = 252;
rq.n_paths = 10'000'000;
optlab::McResult r = optlab::gpu_price(rq);         // r.out[0]: barrier, r.out[1]: same call without barrier
```

## Layout

| Path | Content |
|---|---|
| `include/optlab_cuda/params.hpp` | market, model and product parameters, request and result types |
| `include/optlab_cuda/schemes.hpp` | path schemes and payoffs, shared by CPU and GPU |
| `src/gpu_mc.cu`, `include/optlab_cuda/gpu_mc.cuh` | kernels, reduction, launch logic, study variants |
| `src/cpu_mc.cpp` | double-precision multithreaded reference |
| `src/closed_forms.cpp` | Black-Scholes, geometric Asian, Reiner-Rubinstein barrier, BGK shift |
| `tests/test_prices.cpp` | validation table, float against double, QE bias at 10^8 paths |
| `bench/` | benchmark, optimisation study, NumPy timings, Nsight Compute target |
