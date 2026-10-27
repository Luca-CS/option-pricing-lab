/* Option pricer widget: Black-Scholes, Reiner-Rubinstein barriers (with the Broadie-Glasserman-Kou
   correction for daily monitoring), CRR tree for American options, Monte Carlo for arithmetic Asians
   (geometric control variate), plus SPX results computed offline in Python (pricer-data.js).
   Usage: <div class="pricer-widget" id="pricer"></div> then PricerWidget.mount("pricer"). */
(function () {
  "use strict";

  // ---------- maths
  function ncdf(x) {
    // Hart (1968) as given by West (2005): double-precision accuracy
    var z = Math.abs(x), c = 0;
    if (z <= 37) {
      var e = Math.exp(-z * z / 2);
      if (z < 7.07106781186547) {
        var n = ((((((0.0352624965998911 * z + 0.700383064443688) * z + 6.37396220353165) * z + 33.912866078383) * z +
          112.079291497871) * z + 221.213596169931) * z + 220.206867912376);
        var d = (((((((0.0883883476483184 * z + 1.75566716318264) * z + 16.064177579207) * z + 86.7807322029461) * z +
          296.564248779674) * z + 637.333633378831) * z + 793.826512519948) * z + 440.413735824752);
        c = e * n / d;
      } else {
        var b = z + 0.65;
        b = z + 4 / b; b = z + 3 / b; b = z + 2 / b; b = z + 1 / b;
        c = e / b / 2.506628274631;
      }
    }
    return x > 0 ? 1 - c : c;
  }
  function npdf(x) { return Math.exp(-x * x / 2) / Math.sqrt(2 * Math.PI); }

  function bs(S, K, T, r, q, v, call) {
    var sd = v * Math.sqrt(T);
    var d1 = (Math.log(S / K) + (r - q) * T) / sd + sd / 2, d2 = d1 - sd;
    var phi = call ? 1 : -1, dq = Math.exp(-q * T), dr = Math.exp(-r * T);
    return {
      price: phi * (S * dq * ncdf(phi * d1) - K * dr * ncdf(phi * d2)),
      delta: phi * dq * ncdf(phi * d1),
      gamma: dq * npdf(d1) / (S * sd),
      vega: S * dq * npdf(d1) * Math.sqrt(T) / 100,
      theta: (-S * dq * npdf(d1) * v / (2 * Math.sqrt(T)) - phi * r * K * dr * ncdf(phi * d2) +
        phi * q * S * dq * ncdf(phi * d1)) / 365
    };
  }

  // Continuously monitored single barrier without rebate (Reiner-Rubinstein, as in Haug).
  function barrierOut(S, K, H, T, r, q, v, down, call) {
    var b = r - q, sd = v * Math.sqrt(T), mu = (b - v * v / 2) / (v * v);
    var eta = down ? 1 : -1, phi = call ? 1 : -1;
    var carry = S * Math.exp((b - r) * T), disc = K * Math.exp(-r * T);
    var x1 = Math.log(S / K) / sd + (1 + mu) * sd, x2 = Math.log(S / H) / sd + (1 + mu) * sd;
    var y1 = Math.log(H * H / (S * K)) / sd + (1 + mu) * sd, y2 = Math.log(H / S) / sd + (1 + mu) * sd;
    var p1 = Math.pow(H / S, 2 * (mu + 1)), p2 = Math.pow(H / S, 2 * mu);
    var A = phi * carry * ncdf(phi * x1) - phi * disc * ncdf(phi * x1 - phi * sd);
    var B = phi * carry * ncdf(phi * x2) - phi * disc * ncdf(phi * x2 - phi * sd);
    var C = phi * carry * p1 * ncdf(eta * y1) - phi * disc * p2 * ncdf(eta * y1 - eta * sd);
    var D = phi * carry * p1 * ncdf(eta * y2) - phi * disc * p2 * ncdf(eta * y2 - eta * sd);
    if (down && call) return K > H ? A - C : B - D;
    if (!down && call) return K > H ? 0 : A - B + C - D;
    if (down) return K > H ? A - B + C - D : 0;
    return K > H ? B - D : A - C;
  }

  function barrier(S, K, H, T, r, q, v, kind, call, dailyMonitoring) {
    var down = kind.indexOf("down") === 0, out = kind.slice(-3) === "out";
    if (dailyMonitoring) H = H * Math.exp((down ? -1 : 1) * 0.5825971579 * v * Math.sqrt(1 / 252));
    var vanilla = bs(S, K, T, r, q, v, call).price;
    if (down ? S <= H : S >= H) return out ? 0 : vanilla;
    var o = barrierOut(S, K, H, T, r, q, v, down, call);
    return out ? o : vanilla - o;
  }

  function crr(S, K, T, r, q, v, call, n, american) {
    var dt = T / n, u = Math.exp(v * Math.sqrt(dt)), d = 1 / u;
    var p = (Math.exp((r - q) * dt) - d) / (u - d), disc = Math.exp(-r * dt), phi = call ? 1 : -1;
    var vals = new Float64Array(n + 1), j, i;
    for (j = 0; j <= n; j++) vals[j] = Math.max(phi * (S * Math.pow(u, n - 2 * j) - K), 0);
    for (i = n - 1; i >= 0; i--) {
      for (j = 0; j <= i; j++) {
        var cont = disc * (p * vals[j] + (1 - p) * vals[j + 1]);
        vals[j] = american ? Math.max(cont, phi * (S * Math.pow(u, i - 2 * j) - K)) : cont;
      }
    }
    return vals[0];
  }

  function geoAsian(S, K, T, r, q, v, n, call) {
    var mt = 0, vv = 0, i, j;
    for (i = 1; i <= n; i++) mt += i * T / n;
    mt /= n;
    for (i = 1; i <= n; i++) for (j = 1; j <= n; j++) vv += Math.min(i, j) * T / n;
    vv *= v * v / (n * n);
    var m = Math.log(S) + (r - q - v * v / 2) * mt, sd = Math.sqrt(vv);
    var Fg = Math.exp(m + vv / 2), d1 = Math.log(Fg / K) / sd + sd / 2, phi = call ? 1 : -1;
    return Math.exp(-r * T) * phi * (Fg * ncdf(phi * d1) - K * ncdf(phi * (d1 - sd)));
  }

  function rng(seed) { // mulberry32 + Marsaglia polar: reproducible across browsers
    var a = seed >>> 0, spare = null;
    function u() {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    }
    return function () {
      if (spare !== null) { var s = spare; spare = null; return s; }
      var r, x, y;
      do { x = 2 * u() - 1; y = 2 * u() - 1; r = x * x + y * y; } while (r >= 1 || r === 0);
      var f = Math.sqrt(-2 * Math.log(r) / r);
      spare = y * f;
      return x * f;
    };
  }

  function asianMC(S, K, T, r, q, v, n, call, paths, seed) {
    var z = rng(seed), dt = T / n, drift = (r - q - v * v / 2) * dt, vol = v * Math.sqrt(dt);
    var disc = Math.exp(-r * T), phi = call ? 1 : -1, exactG = geoAsian(S, K, T, r, q, v, n, call);
    var A = new Float64Array(paths), G = new Float64Array(paths), p, i;
    for (p = 0; p < paths; p += 2) {
      var x1 = Math.log(S), x2 = x1, s1 = 0, s2 = 0, l1 = 0, l2 = 0;
      for (i = 0; i < n; i++) {
        var e = z();
        x1 += drift + vol * e; x2 += drift - vol * e;  // antithetic pair
        s1 += Math.exp(x1); s2 += Math.exp(x2); l1 += x1; l2 += x2;
      }
      A[p] = disc * Math.max(phi * (s1 / n - K), 0); A[p + 1] = disc * Math.max(phi * (s2 / n - K), 0);
      G[p] = disc * Math.max(phi * (Math.exp(l1 / n) - K), 0); G[p + 1] = disc * Math.max(phi * (Math.exp(l2 / n) - K), 0);
    }
    var mA = 0, mG = 0, cov = 0, varG = 0, m = paths;
    for (p = 0; p < m; p++) { mA += A[p]; mG += G[p]; }
    mA /= m; mG /= m;
    for (p = 0; p < m; p++) { cov += (A[p] - mA) * (G[p] - mG); varG += (G[p] - mG) * (G[p] - mG); }
    var beta = varG > 0 ? cov / varG : 0, half = m / 2, mean = 0, sq = 0;
    for (p = 0; p < m; p += 2) { // average antithetic pairs before the variance
      var y = 0.5 * ((A[p] - beta * (G[p] - exactG)) + (A[p + 1] - beta * (G[p + 1] - exactG)));
      mean += y; sq += y * y;
    }
    mean /= half;
    var se = Math.sqrt(Math.max(sq / half - mean * mean, 0) / (half - 1));
    return { price: mean, se: se, geo: exactG };
  }

  // ---------- rendering helpers
  var SVGNS = "http://www.w3.org/2000/svg";
  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    for (var k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function svg(tag, attrs, text) {
    var e = document.createElementNS(SVGNS, tag);
    for (var k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function fmt(x, d) { return (Math.round(x * Math.pow(10, d)) / Math.pow(10, d)).toFixed(d); }

  function table(head, rows, numericFrom) {
    var t = el("table", { "class": "pw-table" });
    var tr = el("tr");
    head.forEach(function (h, i) {
      tr.appendChild(el("th", { scope: "col", "class": i >= numericFrom ? "pw-num" : "" }, h));
    });
    t.appendChild(el("thead")).appendChild(tr);
    var body = el("tbody");
    rows.forEach(function (r) {
      var row = el("tr");
      r.forEach(function (c, i) {
        var cell = el(i === 0 ? "th" : "td", i === 0 ? { scope: "row" } : { "class": i >= numericFrom ? "pw-num" : "" });
        if (c && c.nodeType) cell.appendChild(c); else cell.textContent = c;
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
    t.appendChild(body);
    var wrap = el("div", { "class": "pw-scroll" });
    wrap.appendChild(t);
    return wrap;
  }

  // ---------- live pricer
  var FIELDS = [
    ["K", "Strike", 100, 1, 40, 200], ["T", "Maturity (years)", 1, 0.05, 0.05, 5],
    ["v", "Volatility (%)", 20, 0.5, 1, 100], ["r", "Rate (%)", 4, 0.1, -2, 15],
    ["q", "Dividend yield (%)", 1, 0.1, 0, 15], ["H", "Barrier", 80, 1, 1, 400]
  ];
  var KINDS = [["down-out", "Down-and-out"], ["down-in", "Down-and-in"], ["up-out", "Up-and-out"], ["up-in", "Up-and-in"]];

  function selectField(label, name, options) {
    var lab = el("label", { "class": "pw-field" });
    lab.appendChild(el("span", {}, label));
    var sel = el("select", { name: name });
    options.forEach(function (o) { sel.appendChild(el("option", { value: o[0] }, o[1])); });
    lab.appendChild(sel);
    return { label: lab, select: sel };
  }

  function mount(id) {
    var root = document.getElementById(id);
    root.textContent = "";
    var form = el("form", { "class": "pw-form", "aria-label": "Option inputs" });
    form.addEventListener("submit", function (e) { e.preventDefault(); });
    var inputs = {};
    FIELDS.forEach(function (f) {
      var lab = el("label", { "class": "pw-field" });
      lab.appendChild(el("span", {}, f[1]));
      var inp = el("input", { type: "number", step: f[3], min: f[4], max: f[5], value: f[2], name: f[0], inputmode: "decimal" });
      lab.appendChild(inp);
      form.appendChild(lab);
      inputs[f[0]] = inp;
    });
    var cp = selectField("Type", "cp", [["call", "Call"], ["put", "Put"]]);
    var kind = selectField("Barrier type", "kind", KINDS);
    form.appendChild(cp.label);
    form.appendChild(kind.label);
    root.appendChild(el("p", { "class": "pw-note" },
      "Black-Scholes, spot 100. Prices update as you type; the Asian Monte Carlo runs in your browser."));
    root.appendChild(form);

    var out = el("div", { "class": "pw-results", "aria-live": "polite" });
    root.appendChild(out);

    var timer = null;
    function update() {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var K = +inputs.K.value, T = +inputs.T.value, v = +inputs.v.value / 100, r = +inputs.r.value / 100,
          q = +inputs.q.value / 100, H = +inputs.H.value, call = cp.select.value === "call", k = kind.select.value;
        if (!(K > 0 && T > 0 && v > 0 && H > 0)) return;
        var e = bs(100, K, T, r, q, v, call);
        // control variate: the tree's error on the European, known exactly, is removed from the American
        var am = e.price + Math.max(crr(100, K, T, r, q, v, call, 400, true) - crr(100, K, T, r, q, v, call, 400, false), 0);
        var as = asianMC(100, K, T, r, q, v, 12, call, 20000, 7);
        var name = k.charAt(0).toUpperCase() + k.slice(1).replace("-", "-and-");
        var bad = (k.indexOf("down") === 0 && H >= 100) || (k.indexOf("up") === 0 && H <= 100);
        var rows = [
          ["European", "closed form", e.price],
          ["American", "CRR tree, 400 steps, European as control variate; early-exercise premium " + fmt(am - e.price, 4), am],
          ["Asian, 12 fixings", "Monte Carlo, 20,000 paths: ± " + fmt(1.96 * as.se, 4) + " (95 %), geometric control variate", as.price],
          ["Geometric Asian, 12 fixings", "closed form", as.geo]
        ];
        if (bad) {
          rows.push(["Barrier", "a down barrier must be below 100, an up barrier above", NaN]);
        } else {
          rows.push([name + ", H " + H, "daily monitoring: closed form with the Broadie-Glasserman-Kou shift",
            barrier(100, K, H, T, r, q, v, k, call, true)]);
          rows.push([name + ", H " + H, "continuous monitoring (Reiner-Rubinstein)", barrier(100, K, H, T, r, q, v, k, call, false)]);
        }
        render(out, rows, e);
      }, 120);
    }
    form.addEventListener("input", update);
    update();
  }

  function render(out, rows, g) {
    out.textContent = "";
    out.appendChild(table(["Product", "Price"], rows.map(function (r) {
      var wrap = el("span");
      wrap.appendChild(el("span", {}, r[0]));
      wrap.appendChild(el("span", { "class": "pw-sub" }, r[1]));
      return [wrap, isNaN(r[2]) ? "–" : fmt(r[2], 4)];
    }), 1));
    out.appendChild(el("p", { "class": "pw-greeks" },
      "Greeks of the European: delta " + fmt(g.delta, 3) + ", gamma " + fmt(g.gamma, 4) +
      ", vega " + fmt(g.vega, 3) + " per vol point, theta " + fmt(g.theta, 4) + " per day."));
  }

  // ---------- SPX results computed offline
  function mountResults(id) {
    var data = window.PRICER_DATA, root = document.getElementById(id);
    if (!data || !root) return;
    root.textContent = "";
    root.appendChild(table(["Product (1 year, % of spot)"].concat(data.models), data.rows.map(function (r) {
      return [r.product].concat(r.prices.map(function (p) { return fmt(p, 2); }));
    }), 1));
    root.appendChild(el("p", { "class": "pw-note" },
      "Monte Carlo prices; standard errors 0.01 to 0.03. Black-Scholes uses the 1-year ATM implied vol."));
    if (data.mix_sweep && data.mix_sweep.length) {
      var charts = el("div", { "class": "pw-charts" });
      charts.appendChild(lineChart(data.mix_sweep, "dao", "Down-and-out call, H 80 %"));
      charts.appendChild(lineChart(data.mix_sweep, "uao", "Up-and-out call, H 120 %"));
      root.appendChild(charts);
      root.appendChild(el("p", { "class": "pw-note" },
        "SLV price (% of spot) against the mixing fraction: 0 is local vol, 1 the full calibrated Heston vol of " +
        "vol. Each point has its own leverage calibration, so the vanillas stay on the market while the barriers " +
        "move. Band: two standard errors. At 1 the leverage no longer fits the ATM vol (" +
        fmt(data.mix_sweep[data.mix_sweep.length - 1].atm_iv_error_bp, 0) + " bp off): that point is not a valid calibration."));
    }
    if (data.errors) {
      root.appendChild(el("h4", {}, "Fit errors per maturity"));
      root.appendChild(table(["Expiry", "Half bid-ask", "SVI", "Heston", "Local vol", "SLV"], data.errors.map(function (e) {
        return [e.expiry, fmt(e.half_spread, 1), fmt(e.svi, 1), fmt(e.heston, 0), fmt(e.lv, 1), fmt(e.slv, 1)];
      }), 1));
      root.appendChild(el("p", { "class": "pw-note" },
        "Basis points of implied vol. SVI and Heston: RMSE against the quotes. Local vol and SLV: Monte Carlo " +
        "repricing against the SVI surface (300,000 paths, standard error 3 to 4 bp)."));
    }
  }

  function lineChart(points, key, title) {
    var W = 320, Hh = 200, m = { l: 44, r: 12, t: 26, b: 36 };
    var ys = [];
    points.forEach(function (p) { ys.push(p[key] - 2 * p[key + "_se"], p[key] + 2 * p[key + "_se"]); });
    var lo = Math.min.apply(null, ys), hi = Math.max.apply(null, ys), pad = (hi - lo) * 0.1;
    lo -= pad; hi += pad;
    function X(v) { return m.l + v * (W - m.l - m.r); }
    function Y(v) { return Hh - m.b - (v - lo) / (hi - lo) * (Hh - m.t - m.b); }
    var s = svg("svg", { viewBox: "0 0 " + W + " " + Hh, "class": "pw-chart", role: "img", "aria-label": title });
    s.appendChild(svg("text", { x: m.l, y: 14, "class": "pw-ctitle" }, title));
    for (var i = 0; i <= 4; i++) {
      var v = lo + (hi - lo) * i / 4;
      s.appendChild(svg("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), "class": "pw-grid" }));
      s.appendChild(svg("text", { x: m.l - 6, y: Y(v) + 3, "class": "pw-tick", "text-anchor": "end" }, fmt(v, 2)));
    }
    [0, 0.25, 0.5, 0.75, 1].forEach(function (t) {
      s.appendChild(svg("text", { x: X(t), y: Hh - m.b + 14, "class": "pw-tick", "text-anchor": "middle" }, String(t)));
    });
    s.appendChild(svg("text", { x: (m.l + W - m.r) / 2, y: Hh - 4, "class": "pw-tick", "text-anchor": "middle" }, "mixing fraction"));
    var band = points.map(function (p) { return X(p.mix) + "," + Y(p[key] + 2 * p[key + "_se"]); })
      .concat(points.slice().reverse().map(function (p) { return X(p.mix) + "," + Y(p[key] - 2 * p[key + "_se"]); }));
    s.appendChild(svg("polygon", { points: band.join(" "), "class": "pw-band" }));
    s.appendChild(svg("polyline", { points: points.map(function (p) { return X(p.mix) + "," + Y(p[key]); }).join(" "), "class": "pw-line" }));
    points.forEach(function (p) {
      var g = svg("g", { "class": "pw-pt", tabindex: "0" });
      g.appendChild(svg("circle", { cx: X(p.mix), cy: Y(p[key]), r: 12, "class": "pw-hit" }));
      g.appendChild(svg("circle", { cx: X(p.mix), cy: Y(p[key]), r: 4.5, "class": "pw-dot" }));
      g.appendChild(svg("title", {}, "mix " + p.mix + ": " + fmt(p[key], 3) + " ± " + fmt(2 * p[key + "_se"], 3) + " % of spot"));
      s.appendChild(g);
    });
    return s;
  }

  window.PricerWidget = { mount: mount, mountResults: mountResults, bs: bs, barrier: barrier, crr: crr,
    geoAsian: geoAsian, asianMC: asianMC };
})();
