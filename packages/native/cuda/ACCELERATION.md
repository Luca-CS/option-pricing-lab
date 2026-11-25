# Acceleration: where the time goes, and how the engines use the whole machine

Under NumPy, a structured product spends 99 % of its quote in the simulation. The engines move the simulation and the
SLV leverage to compiled code that runs on whatever the machine has: an NVIDIA GPU, every CPU core, or NumPy as
before. NumPy stays the reference: every compiled result is checked against it, or against a closed form, within 4
standard errors.

Measured on the development machine: Ryzen 5 3600 (6 cores, 12 threads, AVX2), 24 GB, RTX 2070 Super (8 GB,
sm_75, display GPU), Windows 11, CUDA 12.9. GPU times after a warm-up (`optlab bench accel`), on an idle machine.

## Hardware tiers

| Machine | Engine (`engine="auto"`) | What runs where |
|---|---|---|
| NVIDIA GPU, CUDA build | `gpu` | paths and SLV leverage on the GPU (float), script evaluation on one thread per physical core |
| Any CPU with a C++17 compiler, the library built without CUDA (`-DOPTLAB_WITH_CUDA=OFF`; tested on x86-64) | `cpu` | paths and leverage on every hardware thread (double) |
| No compiled library | `numpy` | NumPy, optionally on a process pool (`workers=`) |

The CUDA build compiles for the GPUs of the build machine (`OPTLAB_CUDA_ARCHS=native`), or with `portable` for
GTX 10xx (sm_61) to RTX 50xx (sm_120) plus PTX for newer cards. Kernels launch in chunks of tens of milliseconds, so a
display GPU (laptops, desktops driving a screen) never meets the Windows watchdog; output buffers take at most a
quarter of the free memory, so 4 GB laptop GPUs work with smaller chunks. The engine is opt-in in the library
(`engine=` on a pricer, `optlab.native.set_default`, or `$OPTLAB_ENGINE`) and on by default in the desk
(`optlab serve --engine auto`).

## Results

One million paths, Phoenix and BRC on the SPX calibration, worst-of on three local vol names (seconds, wall clock):

| Case | NumPy 1 core | NumPy 6 workers | CPU engine | GPU engine |
|---|---|---|---|---|
| Phoenix 2 years quarterly, local vol | 31.9 | 11.5 | 2.8 | 0.19 |
| Phoenix 2 years quarterly, SLV | 25.2 | 5.5 | 3.7 | 0.19 |
| BRC 1 year, daily barrier, local vol | 23.2 | 6.2 | 1.8 | 0.08 |
| Worst-of Phoenix on 3 names, local vol | 104.5 | 34.8 | 6.4 | 0.29 |
| SLV leverage, 1 year, 200,000 particles | 4.5 | | 1.3 | 0.16 |

A full desk quote of the Phoenix (standard precision, 100,000 paths: local vol base, SLV provision, Greeks with the
SLV vega, the SLV mix band and the hedging costs, so about twenty simulations and seven leverage calibrations):
107.5 s on NumPy (no process pool), 17.5 s on the CPU engine, 4.4 s on the GPU engine. The coupons agree: 4.122 % ±
0.075 on NumPy, 4.053 % ± 0.077 on the engine. The rest of the GPU quote is the NumPy evaluation of the script and the
building of the local variance tables of the bumped surfaces.

Prices agree with NumPy within the Monte Carlo error on independent draws (SLV Phoenix at 4 million paths: 99.4583 ±
0.0054 on NumPy in 22.8 s, 99.4549 ± 0.0054 on the GPU in 2.0 s, 0.45 standard errors apart). The CPU and the GPU use
the same normals: local vol and SLV paths agree to 5e-8 (median, relative), Heston paths too except the 3 % where
float rounding decides whether a variance near zero is truncated; prices agree within a fraction of a standard
error of their paired difference. The leverage calibrated on the engine misses the vanilla surface by the same
amounts as NumPy's (within 1.1 standard errors of each other per strike, at 400,000 paths).

## How it is built

- **One path engine for every model**: Black-Scholes, Dupire local vol on uniform tables, Heston and SLV with full
  truncation, 1 to 8 correlated names, local correlation, Philox counter-based normals; the CPU (double, threads) and
  the GPU (float) run the same header, and a CPU-only build needs no CUDA.
- **`optlab.native` and the batch engine**: `engine=` numpy, cpu, gpu or auto; a fallback to NumPy with its reason;
  batches sized by memory; prices that depend on (seed, paths) only.
- **The SLV leverage by the particle method on the engine**: bins on the device, the regression on the host.
- **Evaluation on several threads while the GPU simulates the next batch** (×4.7 on the Phoenix).
- **Daily windows reduced in the kernel** to the lowest and highest spot: two columns instead of 252 (the BRC ×8 on
  the GPU).
- **Normals read as one stream per pair**, with no draw wasted (the CPU engine ×1.7 to ×2.1).
- **The desk on the engine**: `--engine auto`, the engine shown in the context bar, a 2 million paths precision, the
  mix band's vanilla checks on the engine (a desk quote from 107.5 s to 4.4 s).

## Possible extensions

- A fused payoff on the GPU: the script's AST compiled to CUDA C and loaded with NVRTC, so only pair sums cross PCIe
  (the evaluation is now 60 to 90 % of a GPU quote), gated on equal numbers to the NumPy evaluator on the same paths.
- All the overs of a quote in one launch: vol, skew and correlation scenarios as extra tables on the same draws.
- The CPU engine vectorised over tiles of pairs (AVX2, structure of arrays): it is still about 15 times slower than
  the GPU.
- Other loops: the local correlation calibration, the replay's daily valuations as one batch of markets,
  Longstaff-Schwartz regressions, the Monte Carlo checks of the calibration.

Precision policy: GPU paths in float (consumer cards run double at 1/32 or 1/64 of float), sums in double on the host.
A change that alters prices beyond rounding is validated against NumPy and the closed forms before it becomes a default.
