"""Standalone HTML reports: one file with inline styles and charts, that opens anywhere without the server."""

from __future__ import annotations

import datetime as dt
import html

# Ink on paper for the light theme, slate for the dark one; one blue for marks and links. Tokens first, then the
# two dark blocks (system setting, explicit choice) redefine only the tokens.
CSS = """
:root{--bg:#fbfbfd;--fg:#151922;--muted:#596273;--line:#e1e4ea;--accent:#2457d6;--pos:#16794a;--neg:#b4232c;
--warn:#9a5800;--card:#f2f4f8;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#111318;--fg:#e8eaef;--muted:#9ea7b6;
--line:#2a2f3a;--accent:#7ea4ff;--pos:#4cc38a;--neg:#ff7a7f;--warn:#f0b35a;--card:#181b22;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#111318;--fg:#e8eaef;--muted:#9ea7b6;--line:#2a2f3a;--accent:#7ea4ff;--pos:#4cc38a;
--neg:#ff7a7f;--warn:#f0b35a;--card:#181b22;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,
"Segoe UI",Roboto,sans-serif;font-variant-numeric:tabular-nums}
main{max-width:960px;margin:0 auto;padding-block:32px 40px;padding-left:max(16px,env(safe-area-inset-left,0px));
padding-right:max(16px,env(safe-area-inset-right,0px))}
h1{font-size:26px;margin:0 0 4px;text-wrap:balance}h2{font-size:13px;margin:30px 0 10px;text-transform:uppercase;
letter-spacing:.06em;color:var(--muted)}.muted{color:var(--muted)}a{color:var(--accent)}
.headline{font-size:36px;font-weight:650;margin:18px 0 2px}.card{background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:14px 16px;margin:10px 0}.scroll{overflow-x:auto;margin:6px 0}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:7px 8px;border-bottom:1px solid var(--line);
text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left}td.wrap{white-space:normal;
text-align:left}th{color:var(--muted);font-weight:600}.warn{color:var(--warn)}.pos{color:var(--pos)}
.neg{color:var(--neg)}code,pre{font:13px/1.4 ui-monospace,Consolas,monospace}
pre{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;overflow:auto}
svg text{fill:var(--muted);font-size:11px}svg .grid{stroke:var(--line)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
.tile .k{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.05em}
.tile .v{font-size:22px;font-weight:650;margin-top:2px}.tile .s{color:var(--muted);font-size:13px}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;border:1px solid var(--line)}
.badge.live{color:var(--pos);border-color:var(--pos)}.cols{display:grid;gap:18px;
grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}.cols>*{min-width:0}
ul.events{list-style:none;padding:0;margin:0}ul.events li{display:flex;gap:12px;padding:6px 0;
border-bottom:1px solid var(--line)}ul.events .d{color:var(--muted);width:96px;flex:none}
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


def _page(title: str, body: str, fragment: bool = False) -> str:
    """A standalone document; with `fragment`, the same page without its document skeleton (for hosts that wrap
    the content themselves). Tables scroll inside their own frame on narrow screens."""
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    body = body.replace("<table>", "<div class='scroll'><table>").replace("</table>", "</table></div>")
    main = (f"<main>{body}<p class='muted' style='margin-top:32px'>optlab desk report, generated {stamp}. "
            "Monte Carlo prices carry their standard error; models are calibrated on a personal-use market "
            "snapshot rescaled to a spot of 100.</p></main>")
    head = (f'<title>{_e(title)}</title><meta name="claude:viewport-fit-sides" content="cover">'
            f"<style>{CSS}</style>")
    if fragment:
        return head + main
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1,viewport-fit=cover">{head}</head><body>{main}</body></html>')


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
    hedging = _hedging(q.get("desk"))
    body = (f"<h1>{_e(title)}</h1><p class='muted'>{_e(p['format'])}, strike {_e(p['strike_date'])}, maturity "
            f"{_e(p['maturity'])} · market {_e(q.get('snapshot_id'))}</p>"
            f"<p class='muted' style='margin-bottom:0'>{_e(head['label'])}</p>"
            f"<div class='headline'>{head['value']:.3f} {_e(head['unit'])}<span class='muted' "
            f"style='font-size:18px'>{se}</span></div>"
            f"<div class='card'><b>{_e(pol['label'])}</b><br><span class='muted'>{_e(pol['description'])}. "
            f"{_e(pol['reason'])}.</span></div>{control}{warnings}{hedging}"
            f"<h2>Probability that the product ends at each date</h2>"
            f"{_bars(q['schedule'], lambda r: 100 * r['p_end'], 'probability of ending at each date')}"
            f"<h2>Expected flows by date (% of notional, undiscounted)</h2><table><tr><th>Observation</th>"
            f"<th>Payment</th><th>Ends</th><th>Coupon</th><th>Upside</th><th>Redemption</th></tr>{rows}</table>"
            f"{band}<h2>Term sheet</h2><table>{sheet_rows}</table>"
            f"<h2>Script</h2><pre>{_e(p['script'])}</pre>")
    return _page(title, body)


def _hedging(d: dict | None) -> str:
    """Base, each line with its error and its effect on the coupon, and the client price."""
    if not d:
        return ""

    def row(label, value, se, used="", bp="", cls=""):
        return (f"<tr{cls}><td style='text-align:left'>{_e(label)}</td><td>{_e(used)}</td><td>{value:+.3f}</td><td>{se:.3f}</td><td>{bp}</td></tr>")

    rows = [f"<tr><td style='text-align:left'>Base</td><td></td><td>{d['base_price']:.3f}</td><td>{d['base_se']:.3f}</td><td></td></tr>"]
    for x in d["lines"]:
        used = "" if x.get("used") is None else f"{x['used']:+.4g}" + (" (overridden)" if x.get("overridden") else "")
        bp = "" if x.get("coupon_bp") is None else f"{x['coupon_bp']:+.1f}"
        label = x["label"] + (f" (gap {x['gap']:+.3f}: {x['note']})" if x.get("kind") == "model" and x.get("note") else "")
        label += f" ({x['note']})" if x.get("kind") == "digital" else ""
        rows.append(row(label, x["cost"], x["se"], used, bp))
    rows.append(f"<tr><td style='text-align:left'><b>Client price ({_e(d['side'])})</b></td><td></td><td><b>{d['final_price']:.3f}</b></td>"
                f"<td>{d['final_se']:.3f}</td><td></td></tr>")
    return ("<h2>Hedging cost</h2><p class='muted'>% of notional, seen from the desk: a positive cost is against it. On the base's random numbers.</p>"
            "<table><tr><th style='text-align:left'>Line</th><th>Used</th><th>Cost</th><th>SE</th><th>Coupon, bp</th></tr>" + "".join(rows) + "</table>")


def _reserve_cell(p: dict) -> str:
    r = p.get("reserve")
    return "none booked" if not r else f"{r['amount']:.2f}"


def _reserve_summary(last: dict) -> str:
    if "reserve" not in last:
        return ""
    none = f" {len(last['no_reserve'])} trade(s) booked without one." if last.get("no_reserve") else ""
    return (f"<p>Reserves left {last['reserve']:.2f} of {last['reserve_initial']:.2f} at issue, frozen with each trade, so a value net of reserves of "
            f"{last['value_net']:.2f}.{none}</p>")


def book_report(desk) -> str:
    last = getattr(desk, "last_book", None)
    if last is None:
        return _page("Book", "<h1>Book</h1><p>Price the book in the desk first: the report shows the last run.</p>")
    rows = "".join(f"<tr><td>{_e(p['name'])}</td><td>{_e(p.get('group', ''))}</td><td>{_word(p['class'])}</td>"
                   f"<td>{p['quantity']:g}</td><td>{p['unit_price']:.3f} ± {p['unit_stderr']:.3f}</td>"
                   f"<td>{p['value']:.2f}</td><td>{p['stderr']:.2f}</td><td>{_reserve_cell(p)}</td></tr>" for p in last["positions"])
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
            f"{_reserve_summary(last)}{risk}<table><tr><th>Position</th><th>Group</th><th>Class</th><th>Quantity</th><th>Unit price %</th>"
            f"<th>Value</th><th>SE</th><th>Reserve left</th></tr>{rows}</table>")
    return _page("Book", body)


def _describe(sheet: dict) -> str:
    """One line for a term sheet, as the page writes it (24M · worst of SPX, SX5E · conditional coupon · PDI)."""
    g, c, d = sheet.get("general", {}), sheet.get("coupon", {}), sheet.get("downside", {})
    name = f"worst of {', '.join(g.get('underlyings', []))}" if g.get("basket") == "worst_of" else g.get("underlying")
    parts = [f"{g.get('maturity_months')}M", name or "SPX"]
    if c.get("type") not in (None, "none"):
        solving = (sheet.get("wrapper") or {}).get("solve") == "coupon"
        parts.append(f"{c['type']} coupon" + ("" if solving or c.get("rate") is None else f" {100 * c['rate']:.2f} %"))
    if d.get("type") == "pdi" and d.get("barrier") is not None:
        parts.append(f"PDI {100 * d['barrier']:.0f} % {d.get('observation', '')}".strip())
    if d.get("type") == "geared_put" and d.get("strike") is not None:
        parts.append(f"geared put {100 * d['strike']:.0f} %")
    return " · ".join(parts)


def _line(xs, ys, label: str, xfmt, yfmt) -> str:
    """A line chart in SVG with a light grid and labelled axes (colours from the tokens)."""
    w, h, left, bottom, top = 860, 220, 52, 30, 12
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    pad = (y1 - y0) * 0.1 or 0.01
    y0, y1 = y0 - pad, y1 + pad

    def X(v):
        return left + (w - left - 12) * (v - x0) / ((x1 - x0) or 1)

    def Y(v):
        return top + (h - top - bottom) * (1 - (v - y0) / ((y1 - y0) or 1))

    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{_e(label)}">']
    for k in range(5):
        v = y0 + (y1 - y0) * k / 4
        out.append(f'<line class="grid" x1="{left}" x2="{w - 12}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>'
                   f'<text x="{left - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{_e(yfmt(v))}</text>')
    for k in range(5):
        v = x0 + (x1 - x0) * k / 4
        out.append(f'<text x="{X(v):.1f}" y="{h - 8}" text-anchor="middle">{_e(xfmt(v))}</text>')
    path = " ".join(f"{'M' if i == 0 else 'L'}{X(x):.1f},{Y(y):.1f}" for i, (x, y) in enumerate(zip(xs, ys)))
    out.append(f'<path d="{path}" fill="none" stroke="var(--accent)" stroke-width="2"/>')
    out.append(f'<circle cx="{X(xs[-1]):.1f}" cy="{Y(ys[-1]):.1f}" r="3.5" fill="var(--accent)"/></svg>')
    return "".join(out)


def _tile(key, value, sub="") -> str:
    return (f"<div class='tile'><div class='k'>{_e(key)}</div><div class='v'>{value}</div>"
            f"<div class='s'>{sub}</div></div>")


def desk_snapshot(desk, fragment: bool = False, note: str | None = None) -> str:
    """The whole desk on one page, to send or publish: the market in use, the book (its last valuation and every
    trade), the next observations, the recent quotes and the assumptions for the names that are not calibrated."""
    ctx, term = desk.context(), desk.term_structure()
    trades, quotes = desk.trades()["trades"], desk.quotes()["quotes"][:8]
    upcoming, basket, last = desk.upcoming(60)["events"], desk.basket_settings(), desk.last_book
    v = ctx["atm_vols"]
    market = ("<div class='tiles'>"
              + _tile("Spot", f"{ctx['spot']:.2f}", f"{_e(ctx['underlying'])}, rescaled to 100")
              + _tile("ATM vol 1 year", f"{100 * v['1']:.2f} %",
                      f"3M {100 * v['0.25']:.1f} % · 2Y {100 * v['2']:.1f} %")
              + _tile("Zero rate 1 year", f"{100 * ctx['zero_rates']['1']:.2f} %",
                      f"carry {100 * ctx['carry']['1']:.2f} %")
              + _tile("Calibrated to", f"{ctx['t_max']:.2f} y", f"SLV mixing fraction {ctx['slv_mix']:.2f}") + "</div>"
              + _line(term["T"], [100 * x for x in term["atm_vol"]], "At-the-money volatility by maturity",
                      lambda t: f"{t:.1f}y", lambda y: f"{y:.1f} %"))
    if last:
        g = last.get("greeks") or {}
        risk = "".join(f"<tr><td>{_e(n)}</td><td>{r['delta']:+.3f} ± {r['delta_se']:.3f}</td><td>"
                       + ("–" if r["vega"] is None else f"{r['vega']:+.3f} ± {r['vega_se']:.3f}") + "</td></tr>"
                       for n, r in (g.get("by_name") or {}).items())
        valuation = ("<div class='tiles'>"
                     + _tile("Book value", f"{last['value']:.2f}", f"± {last['stderr']:.2f} (currency)")
                     + _tile("Positions", str(len(last["positions"])),
                             f"{len(last.get('groups', []))} independent simulation(s)")
                     + _tile("Paths", f"{last['n_paths']:,}", f"{last['seconds']:.1f} s with the Greeks") + "</div>"
                     + (f"<table><tr><th>Underlying</th><th>Delta (per +1 % spot)</th><th>Vega (per vol point)</th>"
                        f"</tr>{risk}</table>" if risk else ""))
    else:
        valuation = "<p class='muted'>The book has not been valued since the desk started.</p>"
    rows = "".join(f"<tr><td>{_e(t['trade_id'])}</td><td class='wrap'>"
                   f"{_e(_describe(t['product']['term_sheet']) if t['summary'] else 'script')}</td>"
                   f"<td>{t['quantity']:g}</td><td>{_e(t['strike_date'])}</td>"
                   f"<td><span class='badge {_e(t['status'])}'>{_e(t['status'])}</span></td></tr>" for t in trades)
    trades_html = (f"<table><tr><th>Trade</th><th>Product</th><th>Quantity</th><th>Struck</th><th>Status</th></tr>"
                   f"{rows}</table>" if trades else "<p class='muted'>No trade.</p>")
    if upcoming:
        items = "".join(f"<li><span class='d'>{_e(e['observation'])}</span><span><b>{_e(e['trade_id'])}</b> "
                        f"{'final observation' if e['final'] else 'observation'}"
                        + (f", paid {_e(e['payment'])}" if e.get("payment") else "") + "</span></li>"
                        for e in upcoming[:12])
        events_html = f"<ul class='events'>{items}</ul>"
    else:
        events_html = "<p class='muted'>No observation of a live trade in the next 60 days.</p>"
    if quotes:
        lines = "".join(f"<tr><td>{_e(q['created_at'].replace('T', ' ')[:16])}</td><td class='wrap'>"
                        f"{_e(_describe(q['sheet']))}</td><td>{q['headline']['value']:.3f} ± "
                        f"{q['headline']['se']:.3f} {_e(q['headline']['unit'])}</td></tr>"
                        for q in quotes if q.get("headline"))
        quotes_html = f"<table><tr><th>When (UTC)</th><th>Product</th><th>Result</th></tr>{lines}</table>"
    else:
        quotes_html = "<p class='muted'>No quote.</p>"
    others = ""
    names = basket.get("names", {})
    if names:
        d = basket["defaults"]
        lines = "".join(f"<tr><td>{_e(n)}</td><td>{100 * p['vol']:.1f} %</td><td>{100 * p['q']:.2f} %</td>"
                        f"<td>{100 * (d['r'] if p.get('r') is None else p['r']):.2f} %</td></tr>"
                        for n, p in names.items())
        pairs = " · ".join(f"{k.replace('|', '-')} {r:.2f}" for k, r in basket.get("corr", {}).items())
        pairs = pairs or f"{d['corr']:.2f} for every pair"
        others = (f"<h2>Other underlyings (worst-of inputs)</h2><table><tr><th>Name</th><th>Volatility</th>"
                  f"<th>Dividend</th><th>Rate</th></tr>{lines}</table><p class='muted'>Correlations: "
                  f"{_e(pairs)}</p>")
    body = (f"<h1>Desk snapshot</h1><p class='muted'>Valuation {_e(ctx['valuation'])} · market "
            f"<code>{_e(ctx['snapshot_id'])}</code>" + (f" · {_e(note)}" if note else "") + "</p>"
            f"<h2>Market</h2>{market}<h2>Book</h2>{valuation}{trades_html}"
            f"<div class='cols'><div><h2>Next 60 days</h2>{events_html}</div>"
            f"<div><h2>Recent quotes</h2>{quotes_html}</div></div>{others}")
    return _page("Desk snapshot", body, fragment)
