"""
The compiled engine of optlab (C++, CUDA when built with it): fixed products, paths, SLV leverage.
"""

from collections.abc import Sequence
from typing import Annotated

import numpy
from numpy.typing import NDArray


def gpu_count() -> int:
    """CUDA devices; 0 without device, driver or CUDA build."""

def has_cuda() -> bool:
    """Whether this module was built with the CUDA backend."""

def cpu_threads() -> int:
    """Hardware threads seen by the engine."""

def gpu_name() -> str:
    """Device 0 as 'name (sm_XY, N SM)', or '' without device."""

def device_info(device: int) -> object:
    """The properties of a CUDA device as a dict, or None without it."""

def price(model: int, product: int, legs: Sequence[int], strikes: Sequence[float], H: float, n_steps: int, S0: float, r: float, q: float, T: float, sigma: float, v0: float, kappa: float, theta: float, xi: float, rho: float, n_paths: int, seed: int, backend: int, n_threads: int = 0) -> tuple[list[float], list[float], int, float, float]:
    """
    A fixed product (model 0 Black-Scholes, 1 Heston; product 0 European, 1 Asian, 2 down-and-out barrier) on backend 0 (CPU, double) or 1 (GPU, float): (price[4], se[4], n_paths, kernel_ms, total_ms).
    """

def paths(dt: Annotated[NDArray[numpy.float64], dict(order='C', device='cpu', writable=False)], t0: Annotated[NDArray[numpy.float64], dict(order='C', device='cpu', writable=False)], obs_step: Annotated[NDArray[numpy.int32], dict(order='C', device='cpu', writable=False)], root: Annotated[NDArray[numpy.float64], dict(order='C', device='cpu', writable=False)], kind: Annotated[NDArray[numpy.int32], dict(order='C', device='cpu', writable=False)], cvar: Annotated[NDArray[numpy.float64], dict(order='C', device='cpu', writable=False)], heston: Annotated[NDArray[numpy.float64], dict(order='C', device='cpu', writable=False)], same: int, step_row: Annotated[NDArray[numpy.int32], dict(order='C', device='cpu', writable=False)], row_lo: Annotated[NDArray[numpy.float64], dict(order='C', device='cpu', writable=False)], row_inv_dx: Annotated[NDArray[numpy.float64], dict(order='C', device='cpu', writable=False)], table: Annotated[NDArray[numpy.float64], dict(order='C', device='cpu', writable=False)], fwd: Annotated[NDArray[numpy.float64], dict(order='C', device='cpu', writable=False)], mixer: int, mix: Sequence[float], low_root: Annotated[NDArray[numpy.float64], dict(order='C', device='cpu', writable=False)], n_x: int, n_pairs: int, first_pair: int, seed: int, col_of: Annotated[NDArray[numpy.int32], dict(order='C', device='cpu', writable=False)], n_keep: int, win_lo: Annotated[NDArray[numpy.int32], dict(order='C', device='cpu', writable=False)], win_hi: Annotated[NDArray[numpy.int32], dict(order='C', device='cpu', writable=False)], out: Annotated[NDArray[numpy.float64], dict(order='C', device='cpu')], backend: int, n_threads: int = 0) -> tuple[float, float]:
    """
    Paths of 1 to 8 names (paths.hpp) into `out` (names x columns x 2 n_pairs): (kernel_ms, total_ms). Empty arrays stand for the parts a request does not use (no table, no local correlation, no window).
    """

def leverage(dt: Annotated[NDArray[numpy.float64], dict(order='C', device='cpu', writable=False)], t0: Annotated[NDArray[numpy.float64], dict(order='C', device='cpu', writable=False)], n_pairs: int, seed: int, v0: float, kappa: float, theta: float, xi: float, rho: float, n_bins: int, n_fine: int, atm_vol: float, bandwidth_scale: float, l_min: float, l_max: float, q_lo: float, q_hi: float, local_linear: int, positive_part: int, lv_lo: Annotated[NDArray[numpy.float64], dict(order='C', device='cpu', writable=False)], lv_inv_dx: Annotated[NDArray[numpy.float64], dict(order='C', device='cpu', writable=False)], lv_rows: Annotated[NDArray[numpy.float64], dict(order='C', device='cpu', writable=False)], x_grids: Annotated[NDArray[numpy.float64], dict(order='C', device='cpu')], values: Annotated[NDArray[numpy.float64], dict(order='C', device='cpu')], backend: int, n_threads: int = 0) -> float:
    """
    The SLV leverage by the particle method (leverage.hpp) into x_grids and values (steps x n_x): total_ms.
    """
