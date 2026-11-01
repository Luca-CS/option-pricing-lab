"""Text tables of quotes and results, laid out as a desk reads them."""

from __future__ import annotations

import datetime as dt

TAGS = ("coupon", "upside", "redemption")


def _date(valuation: dt.date | None, t: float) -> str:
    if valuation is None:
        return f"t={t:.3f}"
    return (valuation + dt.timedelta(days=round(t * 365))).isoformat()


def result_table(res, valuation: dt.date | None = None) -> str:
    """Per payment date: probability of redemption and expected flows by tag (undiscounted, % of notional)."""
    N = res.notional
    rows = sorted({t for t, _ in res.expected_flows})
    lines = [f"{'payment':<12} {'P(redeemed)':>12} " + " ".join(f"{t:>11}" for t in TAGS)]
    for t in rows:
        p = res.redemption_probabilities.get(t, 0.0)
        vals = " ".join(f"{100 * res.expected_flows.get((t, tag), 0.0) / N:>10.3f}%" for tag in TAGS)
        lines.append(f"{_date(valuation, t):<12} {100 * p:>11.2f}% {vals}")
    lines.append(f"expected life {res.expected_life:.2f}y")
    return "\n".join(lines)


def _price(res) -> str:
    if res.method == "replication":
        return f"{res.price:.4f} % (replication, grid error {res.numerical_error:.1e} %)"
    return f"{res.price:.4f} +/- {res.stderr:.4f} % ({res.n_paths:,} paths)"


def _format(sheet, fmt: str) -> str:
    if fmt == "swap" and sheet.get("wrapper.funding") == "floating":
        return (f"swap with a floating funding leg + {1e4 * sheet.get('wrapper.funding_spread'):.0f} bp every "
                f"{sheet.get('wrapper.funding_frequency_months')}M until redemption")
    return fmt


def quote_table(q) -> str:
    sheet = q.product.sheet
    lines = [f"{sheet.mask.label} on {', '.join(sheet.underlyings)}, {sheet.get('general.maturity_months')}M, "
             f"{_format(sheet, q.fmt)}, strike {q.product.strike_date}, maturity {q.product.maturity}",
             f"policy: {q.decision.label}", f"  why: {q.profile.description}; {q.decision.reason}"]
    if q.coupon_rate is not None:
        aim = f"note price {q.target:.2f} %" if q.fmt == "note" else f"swap upfront {q.target:.2f} % (zero paid)"
        lines.append(f"coupon solved: {100 * q.coupon_rate:.4f} % p.a. +/- {100 * q.coupon_rate_se:.4f} for {aim}, "
                     f"margin {q.margin:.2f} %")
    else:
        if q.fmt == "note":
            lines.append(f"note price: {q.upfront:.4f} % (implied margin at par {100 - q.upfront:.2f} %)")
        else:
            lines.append(f"upfront paid by the investor: {q.upfront:.4f} % (leg value plus the margin "
                         f"{q.margin:.2f} %)")
    lines.append(f"value: {_price(q.result)}")
    if q.control is not None:
        lines.append(f"control {q.decision.control.upper()}: {_price(q.control)}, gap {q.control_gap_se:+.1f} SE")
    if q.greeks is not None:
        g = q.greeks
        se = (lambda v: "") if g.method == "replication" else (lambda v: f" +/- {v:.4f}")
        vega = f"vega {g.vega:+.4f}{se(g.vega_se)}" if g.vega is not None else g.note
        smooth = ""
        if g.smoothing:
            smooth = (f", digitals smoothed {g.smoothing:.1%} (price bias {g.smoothing_bias:+.4f} +/- "
                      f"{g.smoothing_bias_se:.4f} %)")
        lines.append(f"greeks ({g.model.upper()}): delta (sticky {getattr(g, 'sticky', 'moneyness')}) "
                     f"{g.delta:+.4f}{se(g.delta_se)} % per +1 % spot, {vega} % per vol pt{smooth}")
    if getattr(q, "mix_band", None):
        what = "coupon p.a." if q.coupon_rate is not None else "value"
        cells = [f"mix {m:.2f}: {100 * b['value'] if q.coupon_rate is not None else b['value']:.3f}"
                 f"{' %' if q.coupon_rate is not None else ''} (ATM {b['atm_error_bp']:+.1f} bp)"
                 for m, b in q.mix_band.items()]
        lines.append(f"SLV mix band ({what}): " + "; ".join(cells))
    lines.append(f"time: {q.seconds:.2f} s")
    lines += [f"warning: {w}" for w in q.warnings + q.result.warnings]
    lines += ["", result_table(q.result, q.product.valuation)]
    return "\n".join(lines)
