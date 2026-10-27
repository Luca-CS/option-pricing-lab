/* Option pricer widget: Black-Scholes, CRR tree for American options, Monte Carlo for arithmetic
   Asians (geometric control variate), plus SLV results precomputed in Python (pricer-data.js).
   Usage: <div class="pricer-widget" id="pricer"></div> then PricerWidget.mount("pricer"). */
(function () {
  "use strict";

  // ---------- maths
  function ncdf(x) {
    // Abramowitz-Stegun 7.1.26 on erf, |error| < 1.5e-7
    var t = 1 / (1 + 0.3275911 * Math.abs(x) / Math.SQRT2);
    var y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) *
      t * Math.exp(-x * x / 2);
    return x >= 0 ? 0.5 * (1 + y) : 0.5 * (1 - y);
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

  function rng(seed) { // mulberry32 + Box-Muller: reproducible across browsers
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

  // ---------- rendering
  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    for (var k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function fmt(x, d) { return (Math.round(x * Math.pow(10, d)) / Math.pow(10, d)).toFixed(d); }

  var FIELDS = [
    ["K", "Strike", 100, 1, 40, 200], ["T", "Maturity (years)", 1, 0.05, 0.05, 5],
    ["v", "Volatility (%)", 20, 0.5, 1, 100], ["r", "Rate (%)", 4, 0.1, -2, 15], ["q", "Dividend yield (%)", 1, 0.1, 0, 15]
  ];

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
    var typeLab = el("label", { "class": "pw-field" });
    typeLab.appendChild(el("span", {}, "Type"));
    var sel = el("select", { name: "cp" });
    sel.appendChild(el("option", { value: "put" }, "Put"));
    sel.appendChild(el("option", { value: "call" }, "Call"));
    typeLab.appendChild(sel);
    form.appendChild(typeLab);
    root.appendChild(el("p", { "class": "pw-note" }, "Spot 100. Prices update as you type; the Asian Monte Carlo runs in your browser."));
    root.appendChild(form);

    var out = el("div", { "class": "pw-results", "aria-live": "polite" });
    root.appendChild(out);
    if (window.PRICER_DATA) root.appendChild(slvSection(window.PRICER_DATA));

    var timer = null;
    function update() {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var K = +inputs.K.value, T = +inputs.T.value, v = +inputs.v.value / 100, r = +inputs.r.value / 100,
          q = +inputs.q.value / 100, call = sel.value === "call";
        if (!(K > 0 && T > 0 && v > 0)) return;
        var e = bs(100, K, T, r, q, v, call);
        var am = crr(100, K, T, r, q, v, call, 400, true);
        var as = asianMC(100, K, T, r, q, v, 12, call, 20000, 7);
        render(out, [
          ["European (Black-Scholes)", fmt(e.price, 4), "closed form"],
          ["American (CRR tree, 400 steps)", fmt(am, 4), "early-exercise premium " + fmt(am - e.price, 4)],
          ["Asian, 12 fixings (Monte Carlo)", fmt(as.price, 4), "± " + fmt(1.96 * as.se, 4) + " (95 %), geometric control variate"],
          ["Geometric Asian", fmt(as.geo, 4), "closed form"]
        ], e);
      }, 120);
    }
    form.addEventListener("input", update);
    update();
  }

  function render(out, rows, g) {
    out.textContent = "";
    var t = el("table", { "class": "pw-table" });
    var head = el("tr");
    [["Product", ""], ["Price", "pw-num"]].forEach(function (h) {
      head.appendChild(el("th", { scope: "col", "class": h[1] }, h[0]));
    });
    t.appendChild(el("thead")).appendChild(head);
    var body = el("tbody");
    rows.forEach(function (r) {
      var tr = el("tr");
      var name = el("th", { scope: "row" }, r[0]);
      name.appendChild(el("span", { "class": "pw-sub" }, r[2]));
      tr.appendChild(name);
      tr.appendChild(el("td", { "class": "pw-num" }, r[1]));
      body.appendChild(tr);
    });
    t.appendChild(body);
    out.appendChild(t);
    out.appendChild(el("p", { "class": "pw-greeks" },
      "Greeks of the European: delta " + fmt(g.delta, 3) + ", gamma " + fmt(g.gamma, 4) +
      ", vega " + fmt(g.vega, 3) + " per vol point, theta " + fmt(g.theta, 4) + " per day."));
  }

  function slvSection(data) {
    var sec = el("div", { "class": "pw-slv" });
    sec.appendChild(el("h4", {}, "Same vanillas, different exotics (SPX, " + data.snapshot + ")"));
    sec.appendChild(el("p", { "class": "pw-note" }, "Computed offline in Python: four models calibrated to the same " +
      "SPX smile, prices in % of spot (1 year)."));
    var wrap = el("div", { "class": "pw-scroll" });
    var t = el("table", { "class": "pw-table" });
    var head = el("tr");
    head.appendChild(el("th", { scope: "col" }, "Product"));
    data.models.forEach(function (m) { head.appendChild(el("th", { scope: "col", "class": "pw-num" }, m)); });
    t.appendChild(el("thead")).appendChild(head);
    var body = el("tbody");
    data.rows.forEach(function (r) {
      var tr = el("tr");
      tr.appendChild(el("th", { scope: "row" }, r.product));
      r.prices.forEach(function (p) { tr.appendChild(el("td", { "class": "pw-num" }, fmt(p, 3))); });
      body.appendChild(tr);
    });
    t.appendChild(body);
    wrap.appendChild(t);
    sec.appendChild(wrap);
    return sec;
  }

  window.PricerWidget = { mount: mount, bs: bs, crr: crr, geoAsian: geoAsian, asianMC: asianMC };
})();
