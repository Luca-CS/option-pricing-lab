"""HTML sections of the risk and P&L report, from stored runs (dicts as `RiskStore` keeps them). Amounts in the payment
currency, desk side, each with its standard error; the origin of the market is printed with them."""

from __future__ import annotations

import html

from . import grid


def _e(x) -> str:
    return html.escape(str(x))


def _pm(v, se=None, d=0) -> str:
    if v is None:
        return "–"
    s = f"{v:+,.{d}f}"
    return s if se is None or se == 0 else f"{s} ± {se:,.{d}f}"


def _stat(book: dict, key: str, d=0) -> str:
    v = book.get(key)
    return "–" if v is None else _pm(v[0], v[1], d)


def greeks_section(run: dict) -> str:
    if not run:
        return "<h2>Greeks</h2><p class='muted'>No risk run stored: run one from the Risk screen.</p>"
    book = run["book"]
    names = []
    for k in book:
        g, n = k.split("|")[:2]
        if g == "delta" and n not in ("*", "") and n not in names:
            names.append(n)
    rows = "".join(
        f"<tr><td>{_e(n)}</td><td>{_stat(book, f'delta|{n}|')}</td><td>{_stat(book, f'delta_sticky_strike|{n}|')}</td>"
        f"<td>{_stat(book, f'gamma_5|{n}|')}</td><td>{_stat(book, f'vega|{n}|')}</td><td>{_stat(book, f'vega_weighted|{n}|')}</td>"
        f"<td>{_stat(book, f'vanna|{n}|')}</td><td>{_stat(book, f'volga|{n}|')}</td><td>{_stat(book, f'skew_vega|{n}|')}</td></tr>"
        for n in names)
    tenors = "".join(f"<th>{_e(t)}</th>" for t in grid.TENOR_LABELS)
    vega_rows = "".join(f"<tr><td>{_e(n)}</td>" + "".join(f"<td>{_stat(book, f'vega_tenor|{n}|{t}')}</td>" for t in grid.TENOR_LABELS)
                        + "</tr>" for n in names)
    cur = next((k.split("|")[1] for k in book if k.startswith("rho|")), "USD")
    rho = "".join(f"<td>{_stat(book, f'rho_tenor|{cur}|{t}', 1)}</td>" for t in grid.TENOR_LABELS)
    meta = run.get("meta", {})
    origin = meta.get("origin") or {}
    return (f"<h2>Greeks</h2><p class='muted'>Market {_e(meta.get('snapshot_id'))} ({_e(origin.get('surface', ''))}), valuation "
            f"{_e(meta.get('valuation'))}, {run['settings']['n_paths']:,} paths, base {_e(run['groups'][0]['model'] if run['groups'] else '')}. "
            f"Desk side, currency: delta per +1 % spot, gamma_5 the change of that delta for +1 % over ±5 %, vega per vol point.</p>"
            f"<p>Book value {_stat(book, 'value||')} · theta {_stat(book, 'theta|*|')} per business day · rho "
            f"{_stat(book, f'rho|{cur}|', 1)} per bp · correlation {_stat(book, 'corr|*|')} per point</p>"
            f"<table><tr><th>Name</th><th>Delta</th><th>Delta sticky strike</th><th>Gamma 5 %</th><th>Vega</th><th>Vega weighted</th>"
            f"<th>Vanna</th><th>Volga</th><th>Skew vega</th></tr>{rows}</table>"
            f"<h3>Vega per tenor</h3><table><tr><th>Name</th>{tenors}</tr>{vega_rows}</table>"
            f"<h3>Rho per tenor ({_e(cur)}, per bp)</h3><table><tr>{tenors}</tr><tr>{rho}</tr></table>")


def ladder_section(run: dict) -> str:
    if not run:
        return ""
    head = "".join(f"<th>{p:+.0%}</th>" for p in run["points"])
    rows = ""
    for rule in run["rules"]:
        cells = "".join(f"<td>{_stat(run['book'], f'pnl@{rule}@{p:+.4f}')}</td>" for p in run["points"])
        rows += f"<tr><td>{_e(rule.replace('_', ' '))}</td>{cells}</tr>"
    return f"<h2>Spot ladder</h2><p class='muted'>Book P&L against the base, every spot moved together.</p><table><tr><th>Rule</th>{head}</tr>{rows}</table>"


def scenarios_section(run: dict) -> str:
    if not run:
        return ""
    rows = "".join(f"<tr><td>{_e(s['name'])}</td><td>{_e(s['description'])}</td><td>{_pm(*s['book'])}</td>"
                   f"<td>{'arbitrage: ' + _e(', '.join(s['arbitrage'])) if s['arbitrage'] else ''}</td></tr>" for s in run["scenarios"])
    sign = "starting values, not signed" if run.get("starting") else f"version {run.get('version')}, signed by {_e(run.get('signed_by'))}"
    return f"<h2>Scenarios</h2><p class='muted'>Hypothetical, {sign}.</p><table><tr><th>Scenario</th><th>Moves</th><th>Book P&L</th><th></th></tr>{rows}</table>"


def var_section(run: dict) -> str:
    if not run:
        return ""
    rows = ""
    for k, label in (("one_day", "1 day"), ("ten_day", "10 days (overlapping)"), ("stressed", "stressed, 1 day")):
        r = run.get(k)
        if not r:
            continue
        taylor = f"{r['taylor']['var']:,.0f}" if r.get("taylor") else "–"
        rows += (f"<tr><td>{label}</td><td>{_e(r['start'])} to {_e(r['end'])}</td><td>{r['var']:,.0f}</td><td>{r['var_se']:,.0f}</td>"
                 f"<td>{r['es']:,.0f}</td><td>{_e(r['var_date'])}</td><td>{taylor}</td></tr>")
    vol = {k: v for k, v in run.get("origins", {}).items() if k.startswith("vol:")}
    return (f"<h2>Historical VaR, full revaluation</h2><p class='muted'>99 % VaR and 97.5 % ES of the book's P&L, losses positive; "
            f"vols from surfaces of origin {_e(', '.join(sorted({o for v in vol.values() for o in v})))}.</p>"
            f"<table><tr><th>Horizon</th><th>Window</th><th>VaR</th><th>SE of its scenario</th><th>ES</th><th>VaR day</th>"
            f"<th>Delta-gamma-vega VaR</th></tr>{rows}</table>")


def pnl_section(cum: dict) -> str:
    if not cum or not cum.get("daily"):
        return "<h2>Explained P&L</h2><p class='muted'>No stored P&L run.</p>"
    steps = cum["order"]
    head = "".join(f"<th>{_e(s)}</th>" for s in steps)
    rows = ""
    for d in cum["daily"]:
        cells = "".join(f"<td>{_pm(d['steps'][s][0])}</td>" for s in steps)
        rows += f"<tr><td>{_e(d['to'])}</td><td>{_pm(d['total'])}</td>{cells}<td>{_pm(sum(d['unexplained'].values()))}</td></tr>"
    tot = "".join(f"<td>{_pm(cum['steps'][s])}</td>" for s in steps)
    return (f"<h2>Explained P&L</h2><p class='muted'>Cascade of full revaluations from one close to the next, in this order; unexplained "
            f"= cascade less the prediction of the Greeks of the day before.</p><table><tr><th>Day</th><th>Total</th>{head}"
            f"<th>Unexplained</th></tr>{rows}<tr><td><b>{_e(cum['from'])} to {_e(cum['to'])}</b></td><td><b>{_pm(cum['total'])}</b></td>"
            f"{tot}<td>{_pm(sum(cum['unexplained'].values()))}</td></tr></table>")
