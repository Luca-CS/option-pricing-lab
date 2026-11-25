# ADR-0013: Python 3.14 and C++/CUDA; no Rust

- Status: Accepted, 7 November 2026

## Context

The hot loops are in CUDA/C++ (the path engine, the SLV leverage) and in vectorised NumPy.

## Decision

- Python 3.14 (`requires-python = ">=3.14"`), C++17 and CUDA 12.x for the engine.
- **No Rust**: no decisive gain (the loops that matter are on the GPU or in NumPy), and a Rust engine would double the
  native code to keep bit-identical.
- The front: TypeScript (strict) and React, built by Vite ([ADR-0024](0024-front-end-stack.md)).

## Consequences

- CI runs Python 3.14 only. Every dependency must ship wheels for 3.14 on Windows and Linux.

## Alternatives considered

- Rust (PyO3) for the engine: rejected above.
- Supporting older Pythons too: a matrix for no user.
