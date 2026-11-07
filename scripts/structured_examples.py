"""Structured products on the calibrated SPX: example quotes, the autocall model-risk table and timings.

Console output only. Usage: python scripts/structured_examples.py [--paths 200000] [--quick]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from optlab.core.validation import mc_smile  # noqa: E402
from optlab.structured import Book, Position, PricingContext, compile_sheet, preset, price_many, quote  # noqa: E402
from optlab.structured.greeks import greeks  # noqa: E402
from optlab.structured.multiasset import BasketContext  # noqa: E402
from optlab.structured.solve import combine, solve_affine  # noqa: E402

MODELS = {"bs": "Black-Scholes (ATM vol)", "lv": "Local vol (Dupire)", "heston": "Heston", "slv": "SLV"}


def smile_check(ctx, n_paths):
    print("\n== Vanilla check before reading the table: Monte Carlo implied vols against the SVI surface ==")
    strikes = np.array([70.0, 85.0, 100.0, 115.0])
    for name in ("lv", "slv"):
        for T in (1.0, 2.0):
            iv, se = mc_smile(ctx.model(name, T), ctx.market, T, strikes, n_paths=n_paths, rng=3)
            ref = ctx.surface.iv(np.log(strikes / float(ctx.market.F(T))), T)
            err = (iv - ref) * 100
            print(
                f"  {name.upper():4s} T={T:.0f}y  error (vol pts) "
                + "  ".join(f"K{k:.0f} {e:+.2f}±{s * 100:.2f}" for k, e, s in zip(strikes, err, se))
            )


def model_risk_table(ctx, n_paths, batch):
    sheet = preset("phoenix", **{"coupon.memory": False})
    product = compile_sheet(sheet, ctx.valuation)
    T = product.horizon
    print(
        f"\n== Phoenix {sheet.get('general.maturity_months')}M quarterly, AC 100 %, CB 70 %, KI 60 % European, "
        f"no memory, note, {n_paths:,} paths =="
    )
    results = {}
    for name, label in MODELS.items():
        t0 = time.perf_counter()
        r0, r1 = price_many(
            product.program,
            ctx.model(name, T),
            ctx.market,
            [{"cpn": 0.0}, {"cpn": 1.0}],
            n_paths=n_paths,
            seed=2026,
            batch=batch,
            valuation=ctx.valuation,
        )
        x, x_se = solve_affine(r0, r1, 98.0)
        results[name] = (r0, r1, x, x_se, time.perf_counter() - t0)
    x_ref = results["slv"][2]
    print(
        f"  coupon solved for a price of 98 %; price shown at the SLV coupon "
        f"({100 * product.coupon_rate(x_ref):.3f} % p.a.)"
    )
    print(
        f"  {'model':26s} {'coupon p.a.':>16s} {'price @ SLV cpn':>18s} {'life':>6s} {'E[loss]':>8s}  "
        f"P(call) by date{'':10s} {'time':>6s}"
    )
    for name, (r0, r1, x, x_se, sec) in results.items():
        at = combine(r0, r1, x_ref)
        mat = max(t for t, _ in at.expected_flows)
        alive_end = 1 - at.termination.sum()
        loss = 100 * alive_end - 100 * at.expected_flows.get((mat, "redemption"), 0.0) / at.notional
        calls = " ".join(f"{100 * p:4.1f}" for p in at.termination[1:-1])
        rate, rate_se = 100 * product.coupon_rate(x), 100 * product.coupon_rate(x_se)
        print(
            f"  {MODELS[name]:26s} {rate:8.3f} ± {rate_se:5.3f} {at.price:9.3f} ± {at.stderr:5.3f} "
            f"{at.expected_life:6.2f} {loss:7.2f}%  {calls}  {sec:5.1f}s"
        )


def timings(ctx, n_paths, batch):
    print(f"\n== Timings, Phoenix 24M quarterly, {n_paths:,} paths, one coupon (simulation / evaluation) ==")
    for daily in (False, True):
        sheet = preset("phoenix", **({"downside.observation": "daily"} if daily else {}))
        product = compile_sheet(sheet, ctx.valuation)
        for name in ("bs", "lv", "slv"):
            (r,) = price_many(
                product.program,
                ctx.model(name, product.horizon),
                ctx.market,
                [{}],
                n_paths=n_paths,
                seed=1,
                batch=batch,
                valuation=ctx.valuation,
            )
            ki = "daily KI   " if daily else "European KI"
            print(
                f"  {ki} {name.upper():4s} {r.seconds:6.2f} s  (simulation {r.timings['simulation']:6.2f} s, "
                f"evaluation {r.timings['evaluation']:5.2f} s)  price {r.price:.3f} ± {r.stderr:.3f}"
            )


def example_quotes(ctx, n_paths, batch):
    print("\n== Example quotes with the pricing policy ==")
    for name, changes in [
        ("reverse_convertible", {"downside.barrier": 0.7}),
        ("barrier_reverse_convertible", {}),
        ("phoenix", {}),
        ("athena", {}),
        ("airbag", {"wrapper.format": "swap"}),
        ("capital_protected", {}),
    ]:
        q = quote(preset(name, **changes), ctx, n_paths=n_paths, batch=batch)
        print(f"\n-- {name} --")
        print(q.table())


def phoenix_greeks(ctx, n_paths, batch):
    print("\n== Phoenix 24M Greeks at the SLV coupon (delta per +1 % spot, vega per +1 vol pt, % of notional) ==")
    product = compile_sheet(preset("phoenix"), ctx.valuation)
    for name in ("lv", "slv"):
        g = greeks(product.program, ctx, name, n_paths=n_paths, batch=batch, params={"cpn": 0.02})
        vega = f"vega {g.vega:+.4f} ± {g.vega_se:.4f}" if g.vega is not None else g.note
        print(
            f"  {name.upper():4s} delta {g.delta:+.4f} ± {g.delta_se:.4f}, {vega}; smoothing {g.smoothing:.1%} "
            f"moves the price by {g.smoothing_bias:+.4f} ± {g.smoothing_bias_se:.4f} %  "
            f"({sum(g.seconds.values()):.1f} s)"
        )


def worst_of(ctx, n_paths, batch):
    print(
        "\n== Worst-of Phoenix 24M on SPX (calibrated, local vol) and a synthetic second name (flat vol 22 %, "
        "carry 0) =="
    )
    print("  only SPX is calibrated here: the second name, its vol and the correlation are inputs, not market data")
    single = quote(preset("phoenix"), ctx, control=None, n_paths=n_paths, batch=batch)
    print(
        f"  single SPX (SLV)            coupon {100 * single.coupon_rate:6.3f} ± "
        f"{100 * single.coupon_rate_se:.3f} % p.a."
    )
    for rho in (0.5, 0.7, 0.9):
        basket = BasketContext.with_spx({"SX5E": (100.0, 0.03, 0.03, 0.22)}, [[1, rho], [rho, 1]], ctx)
        q = quote(preset("phoenix", **{"general.basket": "worst_of"}), basket, n_paths=n_paths, batch=batch)
        print(
            f"  worst-of, correlation {rho:.1f}  coupon {100 * q.coupon_rate:6.3f} ± {100 * q.coupon_rate_se:.3f} "
            f"% p.a. (LV), control BS {q.control.price:.3f} ± {q.control.stderr:.3f} % at that coupon, "
            f"{q.seconds:.1f} s"
        )


def book(ctx, n_paths, batch):
    print("\n== Book on one shared simulation ==")
    phoenix = quote(preset("phoenix"), ctx, control=None, n_paths=n_paths, batch=batch)
    positions = [
        Position("Phoenix 24M", phoenix, 10),
        Position("RC 12M", preset("reverse_convertible", **{"wrapper.solve": "upfront"}), -5),
        Position("BRC 12M", preset("barrier_reverse_convertible", **{"wrapper.solve": "upfront"}), 5),
    ]
    b = Book(positions)
    print(b.price(ctx, n_paths=n_paths, batch=batch).table())
    g = b.greeks(ctx, n_paths=n_paths, batch=batch)
    print(
        f"  book delta {g['delta']['total']:+.3f} ± {g['delta']['total_se']:.3f} per +1 % spot "
        f"({g['model'].upper()}); {g.get('note', '')}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", type=int, default=200_000)
    ap.add_argument("--batch", type=int, default=25_000)
    ap.add_argument("--quick", action="store_true", help="20 000 paths, for a smoke run")
    args = ap.parse_args()
    n = 20_000 if args.quick else args.paths
    ctx = PricingContext.from_calibration()
    print(
        f"SPX calibration of {ctx.valuation}, spot rescaled to {ctx.market.spot:g}; last calibrated maturity "
        f"{ctx.t_max:.2f}y"
    )
    t0 = time.perf_counter()
    smile_check(ctx, n)
    model_risk_table(ctx, n, args.batch)
    timings(ctx, n, args.batch)
    phoenix_greeks(ctx, n, args.batch)
    example_quotes(ctx, n, args.batch)
    worst_of(ctx, n, args.batch)
    book(ctx, n, args.batch)
    print(f"\ntotal {time.perf_counter() - t0:.0f} s")


if __name__ == "__main__":
    main()
