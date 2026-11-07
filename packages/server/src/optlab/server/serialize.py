"""Library results as JSON for the page: quotes, results by date, Greeks, books, events, term sheets."""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
from optlab.structured.script.nodes import DateLit
from optlab.structured.script.timeline import Clock, build_timeline


def clean(x):
    """JSON-safe: numpy scalars to Python, NaN and inf to None, dates to ISO strings, tuples to lists."""
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, np.ndarray):
        return clean(x.tolist())
    if isinstance(x, (np.floating, float)):
        x = float(x)
        return x if math.isfinite(x) else None
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, (dt.date, dt.datetime)):
        return x.isoformat()
    return x


def schedule(product, result) -> list[dict]:
    """One row per event after the strike: observation and payment dates, probability that the product ends there,
    expected flows by tag (undiscounted, % of notional)."""
    program = product.program
    timeline = build_timeline(program, Clock(product.valuation, product.calendar))
    N = result.notional
    rows = []
    for k, ev in enumerate(program.events):
        if k == 0:
            continue
        t_pay = timeline.pay_times[k]
        flows = {
            tag: 100 * result.expected_flows.get((t_pay, tag), 0.0) / N for tag in ("coupon", "upside", "redemption")
        }
        rows.append(
            {
                "observation": ev.when.value if isinstance(ev.when, DateLit) else None,
                "payment": ev.pay.value if isinstance(ev.pay, DateLit) else None,
                "t": float(timeline.event_times[k]),
                "p_end": float(result.termination[k]) if len(result.termination) > k else 0.0,
                "p_redeemed": float(result.redemption_probabilities.get(t_pay, 0.0)) if t_pay is not None else 0.0,
                "flows": flows,
            }
        )
    if rows:  # paths alive after the last event end there too: termination only counts explicit terminations
        rows[-1]["p_end"] += max(0.0, 1.0 - float(np.sum(result.termination)))
    return rows


def result_json(res) -> dict | None:
    if res is None:
        return None
    return clean(
        {
            "price": res.price,
            "stderr": res.stderr,
            "method": res.method,
            "n_paths": res.n_paths,
            "seconds": res.seconds,
            "expected_life": res.expected_life,
            "numerical_error": res.numerical_error,
            "warnings": res.warnings,
            "timings": {k: v for k, v in res.timings.items() if isinstance(v, (int, float))},
        }
    )


def greeks_json(g) -> dict | None:
    if g is None:
        return None
    return clean(
        {
            "delta": g.delta,
            "delta_se": g.delta_se,
            "vega": g.vega,
            "vega_se": g.vega_se,
            "smoothing": g.smoothing,
            "smoothing_bias": g.smoothing_bias,
            "method": g.method,
            "model": g.model,
            "note": g.note,
            "units": g.units,
            "sticky": getattr(g, "sticky", "moneyness"),
            "per_name": g.per_name,
        }
    )


def desk_json(desk) -> dict | None:
    return None if desk is None else clean(desk.to_dict())


def quote_json(q) -> dict:
    p, d = q.product, q.decision
    if q.coupon_rate is not None:
        target = f"a note price of {q.target:.2f} %" if q.fmt == "note" else "a zero upfront swap"
        headline = {
            "kind": "coupon",
            "value": 100 * q.coupon_rate,
            "se": 100 * q.coupon_rate_se,
            "unit": "% p.a.",
            "label": f"Coupon solved for {target}",
        }
    else:
        headline = {
            "kind": "upfront",
            "value": q.upfront,
            "se": q.desk.final_se if q.desk else q.result.stderr,
            "unit": "%",
            "label": "Note price" if q.fmt == "note" else "Upfront paid by the investor",
        }
    return clean(
        {
            "headline": headline,
            "product": {
                "type": p.sheet.mask.name,
                "label": p.sheet.mask.label,
                "underlyings": p.sheet.underlyings,
                "maturity_months": p.sheet.get("general.maturity_months"),
                "strike_date": p.strike_date,
                "maturity": p.maturity,
                "format": q.fmt,
                "script": q.script,
            },
            "policy": {
                "class": d.product_class,
                "method": d.method,
                "model": d.model,
                "control": d.control,
                "reason": d.reason,
                "label": d.label,
                "forced": d.forced,
                "n_paths": d.n_paths,
                "provisions": d.provisions,
                "description": q.profile.description,
            },
            "target": q.target,
            "coupon_rate": q.coupon_rate,
            "coupon_rate_se": q.coupon_rate_se,
            "upfront": q.upfront,
            "margin": q.margin,
            "result": result_json(q.result),
            "control": result_json(q.control),
            "control_gap_se": q.control_gap_se,
            "greeks": greeks_json(q.greeks),
            "mix_band": q.mix_band,
            "schedule": schedule(p, q.result),
            "snapshot_id": q.snapshot_id,
            "warnings": q.warnings,
            "desk": desk_json(q.desk),
            "seconds": q.seconds,
            "params": {p.coupon_param: q.coupon_rate * p.coupon_scale}
            if q.coupon_rate is not None and p.coupon_param
            else {},
        }
    )


def book_json(res, trades=None) -> dict:
    positions = []
    for i, pos in enumerate(res.positions):
        row = {
            "name": pos.name,
            "quantity": pos.quantity,
            "unit_price": pos.unit_price,
            "unit_stderr": pos.unit_stderr,
            "value": pos.value,
            "stderr": pos.stderr,
            "class": pos.product_class,
        }
        positions.append(row)
    return clean(
        {
            "value": res.value,
            "stderr": res.stderr,
            "method": res.method,
            "model": res.model,
            "reason": res.reason,
            "n_paths": res.n_paths,
            "seconds": res.seconds,
            "warnings": res.warnings,
            "positions": positions,
            "snapshot_id": getattr(res, "snapshot_id", None),
            "independent_stderr": float(np.sqrt(sum(p.stderr**2 for p in res.positions))),
        }
    )


def trade_json(trade) -> dict:
    sheet = trade.term_sheet()
    summary = None
    if sheet is not None:
        summary = {
            "type": sheet.mask.name,
            "underlyings": sheet.underlyings,
            "maturity_months": sheet.get("general.maturity_months"),
            "coupon": sheet.get("coupon.type"),
            "coupon_rate": sheet.get("coupon.rate"),
            "downside": sheet.get("downside.type"),
            "barrier": sheet.get("downside.barrier"),
            "format": sheet.get("wrapper.format"),
        }
    return clean(
        {
            "trade_id": trade.trade_id,
            "version": trade.version,
            "book": trade.book,
            "status": trade.status,
            "quantity": trade.quantity,
            "trade_date": trade.trade_date,
            "strike_date": trade.strike_date,
            "counterparty": trade.counterparty,
            "tags": trade.tags,
            "product": trade.product,
            "summary": summary,
            "reserves": trade.reserves,
        }
    )


def events_json(events) -> list[dict]:
    return clean([{"date": e.date, "kind": e.kind, "amount": e.amount, "payment": e.payment} for e in events])
