# Documentation

From the overview down. Each level says enough to decide whether to read the next.

## 1. Overview

- [The README](../README.md): what the project is, the demo, the packages, the desk, how to start, the headline
  results.
- [The public demo](https://luca-cs.github.io/option-pricing-lab/): the desk's front on a recorded session, with a
  guided story for the structurer, the trader and the quant.
- [getting-started.md](getting-started.md): from a fresh machine to the desk in production, step by step.

## 2. Architecture

[architecture/README.md](architecture/README.md): the system and its context, the processes (api, worker, proxy), the
packages and their layers, where the state lives, the invariants, and how it is checked.

## 3. Deep dives

| Part | Document |
|---|---|
| The library: simulation, structured products, the desk price, market data, risk | [architecture/library.md](architecture/library.md) |
| The API: signing in, errors, values and units, jobs, pages, serving the front | [architecture/api.md](architecture/api.md) |
| The front: principles, spaces, bridges, design system, the public demo | [ux.md](ux.md), and [frontend/README.md](../frontend/README.md) for its code |
| The GPU engine: method, results, profile | [packages/native/cuda/README.md](../packages/native/cuda/README.md), [ACCELERATION.md](../packages/native/cuda/ACCELERATION.md) |
| Running the desk: accounts, backups, the job queue, containers, releases | [architecture/operations.md](architecture/operations.md) |
| Security: exposure, accounts and roles, requests, headers, data, containers, supply chain | [architecture/security.md](architecture/security.md) |

## 4. Reference

| | |
|---|---|
| [architecture/glossary.md](architecture/glossary.md) | The words of the desk, as the code, the API and the screens use them |
| [architecture/adr/](architecture/adr/README.md) | The architecture decisions, each with its context and the alternatives considered |
| [architecture/feature-inventory.md](architecture/feature-inventory.md) | Every function and where it lives: its route, its command, its screen |
| [api/openapi.json](../api/openapi.json) | The API's contract, versioned and checked against the code |

## 5. Results

Measured, and reproducible by an `optlab` command:

| Report | What it measures |
|---|---|
| [calibration.md](../reports/calibration.md) | The SPX and simulated calibrations: error tables and charts |
| [pricing.md](../reports/pricing.md) | The desk price on examples: base, model reserves, overs, digital overhedges |
| [risk.md](../reports/risk.md) | The risk engine's method, its validation and the showcase's measures |
| [showcase.md](../reports/showcase.md) | A guided tour of the desk on real history |
| [replay.md](../reports/replay.md) | Two products through their lives on real closes |
| [surface_reconstruction.md](../reports/surface_reconstruction.md) | Surfaces rebuilt from volatility indices, against real chains |
| [fx_check.md](../reports/fx_check.md) | FX in the lake, and the size of the quanto adjustment |
