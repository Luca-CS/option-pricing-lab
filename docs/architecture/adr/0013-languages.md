# ADR-0013: Python 3.14 and C++/CUDA; no Rust

- Status: Proposed (A0, 7 November 2026)
- Handoff §1: Langages

## Context

The hot loops are already in CUDA/C++ (path engine, leverage) and in vectorised NumPy. The library runs on Python
3.14.7 in the dev environment.

## Decision

- Python 3.14 (`requires-python = ">=3.14"`), C++17 and CUDA 12.x for the engine.
- **No Rust**: no decisive gain (the loops that matter are on the GPU or in NumPy), and a Rust engine would double the
  native code to keep bit-identical.
- The front of session 2: TypeScript (strict) and React, built by Vite.

## Consequences

- CI runs Python 3.14 only. Dependencies must ship wheels for 3.14 on Windows and Linux; A1 checks every one of them
  (FastAPI, Pydantic, SQLAlchemy, Alembic, argon2-cffi, Typer, uvicorn, duckdb, pyarrow).

## Alternatives considered

- Rust (PyO3) for the engine: rejected above.
- Supporting 3.12 and 3.13 too: a matrix for no user.
