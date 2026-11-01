"""Standalone HTML reports: one file with inline styles and charts, that opens anywhere without the server."""

from __future__ import annotations

import datetime as dt
import html

CSS = """
:root{--bg:#fff;--fg:#14171f;--muted:#5b6474;--line:#e3e6ec;--accent:#2457d6;--warn:#a15c00;--card:#f7f8fa}
@media (prefers-color-scheme:dark){:root{--bg:#111318;--fg:#e8eaef;--muted:#9aa3b2;--line:#2a2f3a;--accent:#7aa2ff;
--warn:#f0b35a;--card:#181b22}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,
"Segoe UI",Roboto,sans-serif;font-variant-numeric:tabular-nums}main{max-width:920px;margin:0 auto;padding:32px 20px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}.muted{color:var(--muted)}
.headline{font-size:36px;font-weight:650;margin:18px 0 2px}.card{background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:14px 16px;margin:10px 0}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right}
th:first-child,td:first-child{text-align:left}
th{color:var(--muted);font-weight:600}.warn{color:var(--warn)}code,pre{font:13px/1.4 ui-monospace,Consolas,monospace}
pre{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;overflow:auto}
svg text{fill:var(--muted);font-size:11px}
"""


def _e(x) -> str:
    return html.escape(str(x))


def _bars(rows, value, label, unit="%") -> str:
    """Horizontal-axis bar chart in SVG."""
    if not rows:
        return ""
    w, h, pad = 860, 180, 28
    vmax = max([value(r) for r in rows] + [1e-9])
    bw = (w - 2 * pad) / len(rows)
    out = [f'<svg viewBox="0 0 {w} {h + 40}" width="100%" role="img" aria-label="{_e(label)}">']
    for i, r in enumerate(rows):
        v = value(r)
        bh = (h - 20) * v / vmax
        x = pad + i * bw + bw * 0.15
        out.append(f'<rect x="{x:.1f}" y="{h - bh:.1f}" width="{bw * 0.7:.1f}" height="{bh:.1f}" rx="2" '
                   f'fill="var(--accent)"><title>{_e(r.get("observation"))}: {v:.2f} {unit}</title></rect>')
        if v > 0:
            out.append(f'<text x="{x + bw * 0.35:.1f}" y="{h - bh - 4:.1f}" text-anchor="middle">{v:.1f}</text>')
        label = _e(str(r.get("observation"))[2:7])
        out.append(f'<text x="{x + bw * 0.35:.1f}" y="{h + 16}" text-anchor="middle">{label}</text>')
    out.append("</svg>")
    return "".join(out)


WORDS = {"monte_carlo": "Monte Carlo", "slv": "SLV", "lv": "local vol", "bs": "Black-Scholes", "heston": "Heston"}


def _word(x) -> str:
    return _e(WORDS.get(x, str(x).replace("_", " ")))


def _page(title: str, body: str) -> str:
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{_e(title)}</title><style>{CSS}</style></head>'
            f'<body><main>{body}<p class="muted" style="margin-top:32px">optlab desk report, generated {stamp}. '
            'Monte Carlo prices carry their standard error; models are calibrated on a personal-use market '
            'snapshot rescaled to a spot of 100.</p></main></body></html>')


def quote_report(saved: dict) -> str:
    q = saved["result"]
    p, pol, head = q["product"], q["policy"], q["headline"]
    title = f"{p['label']} on {', '.join(p['underlyings'])}, {p['maturity_months']}M"
    se = f" ± {head['se']:.3f}" if head.get("se") else ""
    rows = "".join(f"<tr><td>{_e(r['observation'])}</td><td>{_e(r['payment'])}</td><td>{100 * r['p_end']:.1f} %</td>"
                   f"<td>{r['flows']['coupon']:.3f}</td><td>{r['flows']['upside']:.3f}</td>"
                   f"<td>{r['flows']['redemption']:.3f}</td></tr>" for r in q["schedule"])
    sheet_rows = "".join(f"<tr><td>{_e(sec)}</td><td style='text-align:left'>"
                         + ", ".join(f"{_e(k)} = {_e(v)}" for k, v in vals.items()) + "</td></tr>"
                         for sec, vals in q["sheet"].items() if isinstance(vals, dict))
    control = ""
    if q.get("control"):
        control = (f"<p>Control model {_e(pol['control']).upper()}: {q['control']['price']:.3f} ± "
                   f"{q['control']['stderr']:.3f} %, gap {q['control_gap_se']:+.1f} standard errors.</p>")
    band = ""
    if q.get("mix_band"):
        band = "<h2>SLV mixing fraction</h2><table><tr><th>Mix</th><th>Value</th><th>ATM vol error</th></tr>" + "".join(
            f"<tr><td>{float(m):.2f}</td><td>{(100 * b['value'] if head['kind'] == 'coupon' else b['value']):.3f}"
            f"</td><td>{b['atm_error_bp']:+.1f} bp</td></tr>" for m, b in q["mix_band"].items()) + "</table>"
    warnings = "".join(f'<p class="warn">{_e(w)}</p>' for w in q.get("warnings", []))
    body = (f"<h1>{_e(title)}</h1><p class='muted'>{_e(p['format'])}, strike {_e(p['strike_date'])}, maturity "
            f"{_e(p['maturity'])} · market {_e(q.get('snapshot_id'))}</p>"
            f"<p class='muted' style='margin-bottom:0'>{_e(head['label'])}</p>"
            f"<div class='headline'>{head['value']:.3f} {_e(head['unit'])}<span class='muted' "
            f"style='font-size:18px'>{se}</span></div>"
            f"<div class='card'><b>{_e(pol['label'])}</b><br><span class='muted'>{_e(pol['description'])}. "
            f"{_e(pol['reason'])}.</span></div>{control}{warnings}"
            f"<h2>Probability that the product ends at each date</h2>"
            f"{_bars(q['schedule'], lambda r: 100 * r['p_end'], 'probability of ending at each date')}"
            f"<h2>Expected flows by date (% of notional, undiscounted)</h2><table><tr><th>Observation</th>"
            f"<th>Payment</th><th>Ends</th><th>Coupon</th><th>Upside</th><th>Redemption</th></tr>{rows}</table>"
            f"{band}<h2>Term sheet</h2><table>{sheet_rows}</table>"
            f"<h2>Script</h2><pre>{_e(p['script'])}</pre>")
    return _page(title, body)


def book_report(desk) -> str:
    last = getattr(desk, "last_book", None)
    if last is None:
        return _page("Book", "<h1>Book</h1><p>Price the book in the desk first: the report shows the last run.</p>")
    rows = "".join(f"<tr><td>{_e(p['name'])}</td><td>{_e(p.get('group', ''))}</td><td>{_word(p['class'])}</td>"
                   f"<td>{p['quantity']:g}</td><td>{p['unit_price']:.3f} ± {p['unit_stderr']:.3f}</td>"
                   f"<td>{p['value']:.2f}</td><td>{p['stderr']:.2f}</td></tr>" for p in last["positions"])
    g = last.get("greeks") or {}
    risk = ""
    if g.get("by_name"):
        def cell(x, se):
            return "–" if x is None else f"{x:+.3f} ± {se:.3f}"
        lines = "".join(f"<tr><td>{_e(n)}</td><td>{cell(r['delta'], r['delta_se'])}</td>"
                        f"<td>{cell(r['vega'], r['vega_se'])}</td></tr>" for n, r in g["by_name"].items())
        risk = (f"<table><tr><th>Underlying</th><th>Delta ({_e(g['units']['delta'])})</th>"
                f"<th>Vega ({_e(g['units']['vega'])})</th></tr>{lines}</table>"
                + (f"<p class='muted'>{_e(g['note'])}</p>" if g.get("note") else ""))
    groups = "".join(f"<li>{_e(x['label'])}: {x['positions']} position{'s' if x['positions'] > 1 else ''}, "
                     f"{_word(x['method'])} ({_word(x['model'])}), {x['value']:.2f} ± {x['stderr']:.2f}</li>"
                     for x in last.get("groups", []))
    n = len(last["positions"])
    body = (f"<h1>Book</h1><p class='muted'>{n} position{'s' if n > 1 else ''} · {last['n_paths']:,} paths · "
            f"market {_e(last.get('snapshot_id'))}</p>"
            f"<div class='headline'>{last['value']:.2f}<span class='muted' style='font-size:18px'> ± "
            f"{last['stderr']:.2f}</span></div><p class='muted'>Book value in currency. Within a group the positions "
            f"share one simulation, so the error accounts for their correlation (independent legs would give "
            f"{last['independent_stderr']:.2f}); groups are independent.</p><ul>{groups}</ul>"
            f"{risk}<table><tr><th>Position</th><th>Group</th><th>Class</th><th>Quantity</th><th>Unit price %</th>"
            f"<th>Value</th><th>SE</th></tr>{rows}</table>")
    return _page("Book", body)
