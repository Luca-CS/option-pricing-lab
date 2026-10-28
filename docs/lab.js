/* Option pricing lab, browser side.
   Pricers: Black-Scholes, Reiner-Rubinstein barriers with the Broadie-Glasserman-Kou shift, CRR tree with the
   European as control variate, Levy moment matching for arithmetic Asians. Monte Carlo under Black-Scholes or
   the SPX-calibrated Heston model, with Longstaff-Schwartz for American options. The SPX results come from the
   Python library (pricer-data.js). Every closed form here is tested against the Python library. */
(function () {
  "use strict";

  // =====================================================================================  maths
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

  function black(F, K, sd, D, call) {
    if (sd <= 0) return D * Math.max(call ? F - K : K - F, 0);
    var d1 = Math.log(F / K) / sd + sd / 2, phi = call ? 1 : -1;
    return D * phi * (F * ncdf(phi * d1) - K * ncdf(phi * (d1 - sd)));
  }

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
        phi * q * S * dq * ncdf(phi * d1)) / 365,
      rho: phi * K * T * dr * ncdf(phi * d2) / 100
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

  var BGK = 0.5825971579;
  function barrier(S, K, H, T, r, q, v, kind, call, dailyMonitoring) {
    var down = kind.indexOf("down") === 0, out = kind.slice(-3) === "out";
    if (dailyMonitoring) H = H * Math.exp((down ? -1 : 1) * BGK * v * Math.sqrt(1 / 252));
    var vanilla = bs(S, K, T, r, q, v, call).price;
    if (down ? S <= H : S >= H) return out ? 0 : vanilla;
    var o = barrierOut(S, K, H, T, r, q, v, down, call);
    return out ? o : vanilla - o;
  }

  function crr(S, K, T, r, q, v, call, n, american, boundary) {
    var dt = T / n, u = Math.exp(v * Math.sqrt(dt)), d = 1 / u;
    var p = (Math.exp((r - q) * dt) - d) / (u - d), disc = Math.exp(-r * dt), phi = call ? 1 : -1;
    var vals = new Float64Array(n + 1), j, i;
    for (j = 0; j <= n; j++) vals[j] = Math.max(phi * (S * Math.pow(u, n - 2 * j) - K), 0);
    for (i = n - 1; i >= 0; i--) {
      var crit = NaN;
      for (j = 0; j <= i; j++) {
        var cont = disc * (p * vals[j] + (1 - p) * vals[j + 1]);
        if (american) {
          var ex = phi * (S * Math.pow(u, i - 2 * j) - K);
          if (ex > cont && ex > 0) {
            vals[j] = ex;
            // nodes run from the highest spot down: the first exercised node for a put, the last for a call
            if (!call && isNaN(crit)) crit = S * Math.pow(u, i - 2 * j);
            if (call) crit = S * Math.pow(u, i - 2 * j);
          } else vals[j] = cont;
        } else vals[j] = cont;
      }
      if (boundary) boundary[i] = crit;
    }
    return vals[0];
  }

  // American price: the exact European plus the tree's early-exercise premium (control variate).
  function american(S, K, T, r, q, v, call, n) {
    var e = bs(S, K, T, r, q, v, call).price;
    return e + Math.max(crr(S, K, T, r, q, v, call, n, true) - crr(S, K, T, r, q, v, call, n, false), 0);
  }

  function exerciseBoundary(S, K, T, r, q, v, call, n) {
    var b = new Float64Array(n);
    crr(S, K, T, r, q, v, call, n, true, b);
    // the lattice makes the raw boundary saw-toothed: force the monotonicity of the true boundary
    // (rising towards the strike for a put, falling for a call), then average over a few steps
    var pts = [], i, j;
    for (i = 0; i < n; i++) if (!isNaN(b[i])) pts.push([i * T / n, b[i]]);
    for (i = 1; i < pts.length; i++) pts[i][1] = call ? Math.min(pts[i][1], pts[i - 1][1]) : Math.max(pts[i][1], pts[i - 1][1]);
    var out = [];
    for (i = 0; i < pts.length; i++) {
      var s = 0, c = 0;
      for (j = Math.max(0, i - 4); j <= Math.min(pts.length - 1, i + 4); j++) { s += pts[j][1]; c++; }
      out.push([pts[i][0], s / c]);
    }
    return out;
  }

  function geoAsian(S, K, T, r, q, v, n, call) {
    var mt = 0, vv = 0, i, j;
    for (i = 1; i <= n; i++) mt += i * T / n;
    mt /= n;
    for (i = 1; i <= n; i++) for (j = 1; j <= n; j++) vv += Math.min(i, j) * T / n;
    vv *= v * v / (n * n);
    var m = Math.log(S) + (r - q - v * v / 2) * mt;
    return black(Math.exp(m + vv / 2), K, Math.sqrt(vv), Math.exp(-r * T), call);
  }

  // Arithmetic Asian by Levy (1992) moment matching: the average is taken as lognormal with its exact
  // first two moments. Used for the smooth curves and the Greeks; the headline price is Monte Carlo.
  function levyAsian(S, K, T, r, q, v, n, call) {
    var F = [], t = [], i, j, m1 = 0, m2 = 0;
    for (i = 1; i <= n; i++) { t.push(i * T / n); F.push(S * Math.exp((r - q) * i * T / n)); m1 += F[i - 1]; }
    m1 /= n;
    for (i = 0; i < n; i++) for (j = 0; j < n; j++) m2 += F[i] * F[j] * Math.exp(v * v * Math.min(t[i], t[j]));
    m2 /= n * n;
    return black(m1, K, Math.sqrt(Math.log(m2 / (m1 * m1))), Math.exp(-r * T), call);
  }

  // ---- Heston: semi-closed form by the Lewis formula, the reference for the Heston Monte Carlo
  function cx(re, im) { return { re: re, im: im }; }
  function cadd(a, b) { return cx(a.re + b.re, a.im + b.im); }
  function csub(a, b) { return cx(a.re - b.re, a.im - b.im); }
  function cmul(a, b) { return cx(a.re * b.re - a.im * b.im, a.re * b.im + a.im * b.re); }
  function cdiv(a, b) { var d = b.re * b.re + b.im * b.im; return cx((a.re * b.re + a.im * b.im) / d, (a.im * b.re - a.re * b.im) / d); }
  function cexp(a) { var e = Math.exp(a.re); return cx(e * Math.cos(a.im), e * Math.sin(a.im)); }
  function clog(a) { return cx(Math.log(Math.hypot(a.re, a.im)), Math.atan2(a.im, a.re)); }
  function csqrt(a) {
    var r = Math.hypot(a.re, a.im), im = Math.sqrt(Math.max((r - a.re) / 2, 0));
    return cx(Math.sqrt((r + a.re) / 2), a.im < 0 ? -im : im);
  }
  function cscale(a, k) { return cx(a.re * k, a.im * k); }

  // E[exp(i u X_T)] with X_T = ln(S_T / F_T), "little trap" form (Albrecher et al.), u complex
  function hestonCF(u, T, h) {
    var iu = cx(-u.im, u.re), one = cx(1, 0), xi2 = h.xi * h.xi;
    var beta = csub(cx(h.kappa, 0), cscale(iu, h.rho * h.xi));
    var d = csqrt(cadd(cmul(beta, beta), cscale(cadd(iu, cmul(u, u)), xi2)));
    var bm = csub(beta, d), g = cdiv(bm, cadd(beta, d)), e = cexp(cscale(d, -T)), ge = cmul(g, e);
    var C = cscale(csub(cscale(bm, T), cscale(clog(cdiv(csub(one, ge), csub(one, g))), 2)), h.kappa * h.theta / xi2);
    var Dv = cmul(cscale(bm, 1 / xi2), cdiv(csub(one, e), csub(one, ge)));
    return cexp(cadd(C, cscale(Dv, h.v0)));
  }

  var GL = (function (n) { // Gauss-Legendre nodes and weights on [-1, 1]
    var x = [], w = [];
    for (var i = 1; i <= n; i++) {
      var z = Math.cos(Math.PI * (i - 0.25) / (n + 0.5)), z1, pp;
      do {
        var p1 = 1, p2 = 0;
        for (var j = 1; j <= n; j++) { var p3 = p2; p2 = p1; p1 = ((2 * j - 1) * z * p2 - (j - 1) * p3) / j; }
        pp = n * (z * p1 - p2) / (z * z - 1); z1 = z; z = z1 - p1 / pp;
      } while (Math.abs(z - z1) > 1e-15);
      x.push(z); w.push(2 / ((1 - z * z) * pp * pp));
    }
    return { x: x, w: w };
  })(64);

  // Lewis (2001): C = D [F - sqrt(F K) / pi * integral of Re(e^{iu ln(F/K)} phi(u - i/2)) / (u^2 + 1/4)]
  function hestonPrice(S, K, T, r, q, h, call) {
    var F = S * Math.exp((r - q) * T), D = Math.exp(-r * T), lk = Math.log(F / K), s = 0;
    var uMax = Math.min(Math.max(40 / Math.sqrt(Math.max(h.v0, h.theta, 1e-4) * T), 60), 5000), panels = 8;
    for (var p = 0; p < panels; p++) {
      var a = uMax * p / panels, b = uMax * (p + 1) / panels;
      for (var i = 0; i < GL.x.length; i++) {
        var u = 0.5 * (b - a) * GL.x[i] + 0.5 * (b + a);
        var ph = cmul(cx(Math.cos(u * lk), Math.sin(u * lk)), hestonCF(cx(u, -0.5), T, h));
        s += 0.5 * (b - a) * GL.w[i] * ph.re / (u * u + 0.25);
      }
    }
    var c = D * (F - Math.sqrt(F * K) / Math.PI * s);
    return call ? c : c - D * (F - K);
  }

  function impliedVol(price, S, K, T, r, q, call) { // bisection
    var lo = 1e-4, hi = 4;
    for (var i = 0; i < 100; i++) {
      var mid = 0.5 * (lo + hi);
      if (bs(S, K, T, r, q, mid, call).price > price) hi = mid; else lo = mid;
    }
    return 0.5 * (lo + hi);
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
        x1 += drift + vol * e; x2 += drift - vol * e;
        s1 += Math.exp(x1); s2 += Math.exp(x2); l1 += x1; l2 += x2;
      }
      A[p] = disc * Math.max(phi * (s1 / n - K), 0); A[p + 1] = disc * Math.max(phi * (s2 / n - K), 0);
      G[p] = disc * Math.max(phi * (Math.exp(l1 / n) - K), 0); G[p + 1] = disc * Math.max(phi * (Math.exp(l2 / n) - K), 0);
    }
    var mA = 0, mG = 0, cov = 0, varG = 0;
    for (p = 0; p < paths; p++) { mA += A[p]; mG += G[p]; }
    mA /= paths; mG /= paths;
    for (p = 0; p < paths; p++) { cov += (A[p] - mA) * (G[p] - mG); varG += (G[p] - mG) * (G[p] - mG); }
    var beta = varG > 0 ? cov / varG : 0, half = paths / 2, mean = 0, sq = 0;
    for (p = 0; p < paths; p += 2) {
      var y = 0.5 * ((A[p] - beta * (G[p] - exactG)) + (A[p + 1] - beta * (G[p + 1] - exactG)));
      mean += y; sq += y * y;
    }
    mean /= half;
    return { price: mean, se: Math.sqrt(Math.max(sq / half - mean * mean, 0) / (half - 1)), geo: exactG };
  }

  // =====================================================================================  products
  var PRODUCTS = {
    european: { label: "European", method: "Black-Scholes closed form" },
    american: { label: "American", method: "CRR tree, European as control variate" },
    asian: { label: "Asian", method: "Monte Carlo with a geometric control variate; curves by Lévy moment matching" },
    barrier: { label: "Barrier", method: "Reiner-Rubinstein, Broadie-Glasserman-Kou shift for daily monitoring" }
  };

  // Smooth semi-analytic value, used for the curves and the Greeks.
  function value(st, over) {
    var p = Object.assign({}, st, over || {});
    var T = Math.max(p.T, 1 / 365);
    if (p.product === "american") return american(p.S, p.K, T, p.r, p.q, p.v, p.call, p.treeSteps || 150);
    if (p.product === "asian") return levyAsian(p.S, p.K, T, p.r, p.q, p.v, 12, p.call);
    if (p.product === "barrier") return barrier(p.S, p.K, p.H, T, p.r, p.q, p.v, p.kind, p.call, true);
    return bs(p.S, p.K, T, p.r, p.q, p.v, p.call).price;
  }

  function greeks(st) {
    if (st.product === "european") return bs(st.S, st.K, st.T, st.r, st.q, st.v, st.call);
    var h = 0.01 * st.S, f0 = value(st);
    var up = value(st, { S: st.S + h }), dn = value(st, { S: st.S - h });
    return {
      price: f0,
      delta: (up - dn) / (2 * h),
      gamma: (up - 2 * f0 + dn) / (h * h),
      vega: (value(st, { v: st.v + 0.01 }) - value(st, { v: Math.max(st.v - 0.01, 0.005) })) / 2,
      theta: st.T > 2 / 365 ? value(st, { T: st.T - 1 / 365 }) - f0 : NaN,
      rho: (value(st, { r: st.r + 0.01 }) - value(st, { r: st.r - 0.01 })) / 2
    };
  }

  function headline(st) {
    if (st.product === "american") return { price: american(100, st.K, st.T, st.r, st.q, st.v, st.call, 600) };
    if (st.product === "asian") {
      var a = asianMC(100, st.K, st.T, st.r, st.q, st.v, 12, st.call, 40000, 11);
      return { price: a.price, se: a.se };
    }
    return { price: value(st) };
  }

  // =====================================================================================  Monte Carlo
  // Paths on a grid of nSteps, under Black-Scholes (exact log steps) or Heston (full-truncation Euler).
  function simulate(st, nPaths, nSteps, seed) {
    var z = rng(seed), T = st.T, dt = T / nSteps, sq = Math.sqrt(dt), mu = st.r - st.q;
    var S = new Float64Array(nPaths * (nSteps + 1)), half = nPaths / 2, p, i;
    var hp = st.model === "heston" ? st.heston : null;
    var buf1 = new Float64Array(nSteps), buf2 = new Float64Array(nSteps);
    for (p = 0; p < half; p++) {
      for (i = 0; i < nSteps; i++) { buf1[i] = z(); buf2[i] = hp ? z() : 0; }
      for (var a = 0; a < 2; a++) {
        var sgn = a === 0 ? 1 : -1, row = (p + a * half) * (nSteps + 1), x = Math.log(100), vv = hp ? hp.v0 : 0;
        S[row] = 100;
        for (i = 0; i < nSteps; i++) {
          if (hp) {
            var vp = Math.max(vv, 0), z2 = sgn * buf2[i], zx = hp.rho * z2 + Math.sqrt(1 - hp.rho * hp.rho) * sgn * buf1[i];
            x += (mu - vp / 2) * dt + Math.sqrt(vp) * sq * zx;
            vv += hp.kappa * (hp.theta - vp) * dt + hp.xi * Math.sqrt(vp) * sq * z2;
          } else {
            x += (mu - st.v * st.v / 2) * dt + st.v * sq * sgn * buf1[i];
          }
          S[row + i + 1] = Math.exp(x);
        }
      }
    }
    return { S: S, n: nPaths, steps: nSteps, dt: dt, seed: seed };
  }

  function solve4(A, b) { // Gaussian elimination with partial pivoting
    var n = 4, M = A.map(function (row, i) { return row.concat([b[i]]); }), i, j, k;
    for (i = 0; i < n; i++) {
      var piv = i;
      for (k = i + 1; k < n; k++) if (Math.abs(M[k][i]) > Math.abs(M[piv][i])) piv = k;
      var tmp = M[i]; M[i] = M[piv]; M[piv] = tmp;
      if (Math.abs(M[i][i]) < 1e-14) return null;
      for (k = i + 1; k < n; k++) {
        var f = M[k][i] / M[i][i];
        for (j = i; j <= n; j++) M[k][j] -= f * M[i][j];
      }
    }
    var x = [0, 0, 0, 0];
    for (i = n - 1; i >= 0; i--) {
      var s = M[i][n];
      for (j = i + 1; j < n; j++) s -= M[i][j] * x[j];
      x[i] = s / M[i][i];
    }
    return x;
  }

  // Regression coefficients of the continuation value at each exercise date, backward on a set of paths.
  function lsmRule(st, sim) {
    var n = sim.n, m = sim.steps, S = sim.S, K = st.K, phi = st.call ? 1 : -1, i, p;
    var df = Math.exp(-st.r * sim.dt), cash = new Float64Array(n), rule = new Array(m);
    for (p = 0; p < n; p++) cash[p] = Math.max(phi * (S[p * (m + 1) + m] / K - 1), 0);
    for (i = m - 1; i >= 1; i--) {
      for (p = 0; p < n; p++) cash[p] *= df;
      var A = [[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]], b = [0, 0, 0, 0], idx = [];
      for (p = 0; p < n; p++) {
        var xr = S[p * (m + 1) + i] / K;
        if (phi * (xr - 1) <= 0) continue;
        idx.push(p);
        var f = [1, xr, xr * xr, xr * xr * xr];
        for (var a = 0; a < 4; a++) { b[a] += f[a] * cash[p]; for (var c = 0; c < 4; c++) A[a][c] += f[a] * f[c]; }
      }
      if (idx.length < 20) continue;
      var beta = solve4(A, b);
      if (!beta) continue;
      rule[i] = beta;
      idx.forEach(function (pp) {
        var x2 = S[pp * (m + 1) + i] / K, e2 = phi * (x2 - 1);
        if (e2 > beta[0] + x2 * (beta[1] + x2 * (beta[2] + x2 * beta[3]))) cash[pp] = e2;
      });
    }
    return rule;
  }

  // Discounted payoff of every path, plus per-path events (knock-out time, exercise time) for the chart.
  function payoffs(st, sim) {
    var n = sim.n, m = sim.steps, S = sim.S, K = st.K, phi = st.call ? 1 : -1, r = st.r, T = st.T;
    var pay = new Float64Array(n), event = new Int32Array(n).fill(-1), p, i;
    var disc = Math.exp(-r * T);
    if (st.product === "european") {
      for (p = 0; p < n; p++) pay[p] = disc * Math.max(phi * (S[p * (m + 1) + m] - K), 0);
    } else if (st.product === "asian") {
      var every = m / 12;
      for (p = 0; p < n; p++) {
        var s = 0;
        for (i = 1; i <= 12; i++) s += S[p * (m + 1) + Math.round(i * every)];
        pay[p] = disc * Math.max(phi * (s / 12 - K), 0);
      }
    } else if (st.product === "barrier") {
      var down = st.kind.indexOf("down") === 0, out = st.kind.slice(-3) === "out";
      for (p = 0; p < n; p++) {
        var hit = -1;
        for (i = 1; i <= m; i++) {
          var x = S[p * (m + 1) + i];
          if (down ? x <= st.H : x >= st.H) { hit = i; break; }
        }
        event[p] = hit;
        var alive = out ? hit < 0 : hit >= 0;
        pay[p] = alive ? disc * Math.max(phi * (S[p * (m + 1) + m] - K), 0) : 0;
      }
    } else { // American: Longstaff-Schwartz, cubic basis in S/K on in-the-money paths
      // The exercise rule is regressed on independent paths, then applied to the pricing paths: an in-sample
      // rule sees the future of the paths it prices and biases the price up.
      var rule = lsmRule(st, simulate(st, n, m, sim.seed + 7919));
      var df = Math.exp(-r * sim.dt);
      for (p = 0; p < n; p++) {
        var row = p * (m + 1), done = false;
        for (i = 1; i < m && !done; i++) {
          var xr = S[row + i] / K, ex = phi * (xr - 1), bt = rule[i];
          if (ex > 0 && bt && ex > bt[0] + xr * (bt[1] + xr * (bt[2] + xr * bt[3]))) {
            pay[p] = K * ex * Math.pow(df, i); event[p] = i; done = true;
          }
        }
        if (!done) pay[p] = K * Math.max(phi * (S[row + m] / K - 1), 0) * Math.pow(df, m);
      }
    }
    return { pay: pay, event: event };
  }

  // Running estimate and 95 % band, antithetic pairs averaged first.
  function convergence(pay, every) {
    var half = pay.length / 2, out = [], s = 0, s2 = 0;
    for (var k = 0; k < half; k++) {
      var y = 0.5 * (pay[k] + pay[k + half]);
      s += y; s2 += y * y;
      var n = k + 1;
      if (n % every === 0 || n === half) {
        var m = s / n, se = n > 1 ? Math.sqrt(Math.max(s2 / n - m * m, 0) / (n - 1)) : 0;
        out.push([2 * n, m, se]);
      }
    }
    return out;
  }

  // =====================================================================================  DOM helpers
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
  function fmt(x, d) {
    if (!isFinite(x)) return "–";
    var s = (Math.round(x * Math.pow(10, d)) / Math.pow(10, d)).toFixed(d);
    return s === (-0).toFixed(d) ? (0).toFixed(d) : s.replace("-", "−");
  }
  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function niceTicks(lo, hi, n) {
    var span = hi - lo, step = Math.pow(10, Math.floor(Math.log10(span / n))), err = n / span * step;
    if (err <= 0.15) step *= 10; else if (err <= 0.35) step *= 5; else if (err <= 0.75) step *= 2;
    var out = [];
    for (var v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // =====================================================================================  line chart (SVG)
  // series: [{points: [[x, y]], cls, label, dashed}], with a crosshair and a tooltip on hover.
  function lineChart(host, opts) {
    host.textContent = "";
    // drawn at the host's pixel width so that text keeps its size
    var W = Math.max(260, Math.round(host.clientWidth) || opts.width || 640), H = opts.height || 280, m = { l: 52, r: 14, t: 14, b: 34 };
    var all = [];
    opts.series.forEach(function (s) { s.points.forEach(function (p) { if (isFinite(p[1])) all.push(p); }); });
    if (!all.length) return;
    var xs = all.map(function (p) { return p[0]; }), ys = all.map(function (p) { return p[1]; });
    if (opts.band) opts.band.forEach(function (b) { ys.push(b[1], b[2]); });
    if (opts.refLines) opts.refLines.forEach(function (rl) { if (rl.axis === "y") ys.push(rl.value); });
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
    var y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    if (opts.zero) { y0 = Math.min(y0, 0); y1 = Math.max(y1, 0); }
    if (x1 - x0 < 1e-9) x1 = x0 + 1; // a single point, e.g. the first frame of the convergence chart
    if (y1 - y0 < 1e-9) { y0 -= 1; y1 += 1; }
    var pad = (y1 - y0) * 0.08; y0 -= pad; y1 += pad;
    function X(v) { return m.l + (v - x0) / (x1 - x0) * (W - m.l - m.r); }
    function Y(v) { return H - m.b - (v - y0) / (y1 - y0) * (H - m.t - m.b); }
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, "class": "lab-svg", role: "img", "aria-label": opts.label || "" });
    niceTicks(y0, y1, 5).forEach(function (v) {
      s.appendChild(svg("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), "class": Math.abs(v) < 1e-12 ? "lab-zero" : "lab-grid" }));
      s.appendChild(svg("text", { x: m.l - 8, y: Y(v) + 3.5, "class": "lab-tick", "text-anchor": "end" }, opts.yFmt ? opts.yFmt(v) : String(v)));
    });
    niceTicks(x0, x1, 6).forEach(function (v) {
      s.appendChild(svg("text", { x: X(v), y: H - m.b + 16, "class": "lab-tick", "text-anchor": "middle" }, opts.xFmt ? opts.xFmt(v) : String(v)));
    });
    if (opts.xLabel) s.appendChild(svg("text", { x: W - m.r, y: H - 3, "class": "lab-tick", "text-anchor": "end" }, opts.xLabel));
    var xRefs = 0, lastLabelY = null;
    (opts.refLines || []).forEach(function (rl) {
      if (rl.axis === "x" && rl.value >= x0 && rl.value <= x1) {
        s.appendChild(svg("line", { x1: X(rl.value), x2: X(rl.value), y1: m.t, y2: H - m.b, "class": rl.cls }));
        s.appendChild(svg("text", { x: X(rl.value) + 4, y: m.t + 10 + 13 * xRefs++, "class": "lab-ref-label" }, rl.label));
      }
      if (rl.axis === "y") {
        s.appendChild(svg("line", { x1: m.l, x2: W - m.r, y1: Y(rl.value), y2: Y(rl.value), "class": rl.cls }));
        var ly = Y(rl.value) - 5;
        if (lastLabelY !== null && Math.abs(ly - lastLabelY) < 13) ly = Y(rl.value) + 14; // keep labels apart
        lastLabelY = ly;
        s.appendChild(svg("text", { x: W - m.r, y: ly, "class": "lab-ref-label", "text-anchor": "end" }, rl.label));
      }
    });
    if (opts.band && opts.band.length) {
      var pts = opts.band.map(function (b) { return X(b[0]) + "," + Y(b[2]); })
        .concat(opts.band.slice().reverse().map(function (b) { return X(b[0]) + "," + Y(b[1]); }));
      s.appendChild(svg("polygon", { points: pts.join(" "), "class": "lab-band " + (opts.bandCls || "") }));
    }
    opts.series.forEach(function (se) {
      var d = "", pen = false;
      se.points.forEach(function (p) {
        if (!isFinite(p[1])) { pen = false; return; }
        d += (pen ? "L" : "M") + X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1);
        pen = true;
      });
      s.appendChild(svg("path", { d: d, "class": "lab-line " + (se.cls || "") + (se.dashed ? " lab-dashed" : "") }));
    });
    // hover layer
    var cross = svg("line", { y1: m.t, y2: H - m.b, "class": "lab-cross", visibility: "hidden" });
    s.appendChild(cross);
    var dots = opts.series.map(function (se) {
      var c = svg("circle", { r: 4, "class": "lab-hdot " + (se.cls || ""), visibility: "hidden" });
      s.appendChild(c);
      return c;
    });
    var hit = svg("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, "class": "lab-hit" });
    s.appendChild(hit);
    host.appendChild(s);
    var tip = el("div", { "class": "lab-tip", hidden: "" });
    host.appendChild(tip);
    function move(clientX) {
      var rect = s.getBoundingClientRect(), xv = x0 + ((clientX - rect.left) / rect.width * W - m.l) / (W - m.l - m.r) * (x1 - x0);
      xv = Math.max(x0, Math.min(x1, xv));
      var lines = [(opts.xName || "x") + " " + (opts.xFmt ? opts.xFmt(xv) : fmt(xv, 2))];
      cross.setAttribute("x1", X(xv)); cross.setAttribute("x2", X(xv)); cross.setAttribute("visibility", "visible");
      opts.series.forEach(function (se, k) {
        var best = null;
        se.points.forEach(function (p) { if (isFinite(p[1]) && (!best || Math.abs(p[0] - xv) < Math.abs(best[0] - xv))) best = p; });
        if (!best) return;
        dots[k].setAttribute("cx", X(best[0])); dots[k].setAttribute("cy", Y(best[1])); dots[k].setAttribute("visibility", "visible");
        lines.push(se.label + " " + (opts.tipFmt ? opts.tipFmt(best[1]) : fmt(best[1], 4)));
      });
      tip.textContent = "";
      lines.forEach(function (l, i) { tip.appendChild(el(i ? "span" : "strong", {}, l)); });
      tip.hidden = false;
      var px = (X(xv) / W) * rect.width;
      tip.style.left = Math.min(Math.max(px + 12, 0), rect.width - tip.offsetWidth) + "px";
    }
    function leave() { tip.hidden = true; cross.setAttribute("visibility", "hidden"); dots.forEach(function (d) { d.setAttribute("visibility", "hidden"); }); }
    hit.addEventListener("pointermove", function (e) { move(e.clientX); });
    hit.addEventListener("pointerleave", leave);
  }

  // =====================================================================================  Monte Carlo canvas
  function drawPaths(canvas, st, sim, events, terminal, progress, extras) {
    var dpr = window.devicePixelRatio || 1, rect = canvas.getBoundingClientRect();
    var W = rect.width, H = rect.height;
    if (canvas.width !== Math.round(W * dpr)) { canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr); }
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    var histW = Math.min(110, W * 0.2), m = { l: 40, r: histW + 10, t: 12, b: 26 };
    var lo = extras.lo, hi = extras.hi;
    function X(i) { return m.l + i / sim.steps * (W - m.l - m.r); }
    // linear price axis, shared by both models so that the shapes compare
    function Y(v) { return H - m.b - (v - lo) / (hi - lo) * (H - m.t - m.b); }
    var ink = css("--text"), muted = css("--muted"), line = css("--line"), series = css("--lab-series"), warn = css("--lab-warn");
    ctx.font = "11px " + css("--sans");
    ctx.lineWidth = 1;
    niceTicks(lo, hi, 5).forEach(function (v) {
      if (v <= lo || v >= hi) return;
      ctx.strokeStyle = line; ctx.beginPath(); ctx.moveTo(m.l, Y(v)); ctx.lineTo(W - m.r, Y(v)); ctx.stroke();
      ctx.fillStyle = muted; ctx.textAlign = "right"; ctx.fillText(String(v), m.l - 6, Y(v) + 4);
    });
    ctx.textAlign = "center";
    ctx.fillText("0", X(0), H - 8);
    ctx.fillText(fmt(st.T, 2) + " y", X(sim.steps), H - 8);
    var upto = Math.max(1, Math.floor(progress * sim.steps));
    // paths, clipped to the plot area
    ctx.save();
    ctx.beginPath(); ctx.rect(m.l, m.t, W - m.l - m.r, H - m.t - m.b); ctx.clip();
    var nShow = extras.nShow;
    for (var p = 0; p < nShow; p++) {
      var row = p * (sim.steps + 1), ev = events[p], dead = st.product === "barrier" && st.kind.slice(-3) === "out";
      ctx.beginPath();
      ctx.moveTo(X(0), Y(sim.S[row]));
      var stop = upto;
      if (ev >= 0 && ev < upto) stop = ev;
      for (var i = 1; i <= stop; i++) ctx.lineTo(X(i), Y(sim.S[row + i]));
      ctx.strokeStyle = series; ctx.globalAlpha = 0.28; ctx.lineWidth = 1; ctx.stroke();
      if (ev >= 0 && ev < upto) {
        if (dead || st.product === "american") { // faded after the knock-out or the exercise
          ctx.beginPath(); ctx.moveTo(X(ev), Y(sim.S[row + ev]));
          for (i = ev + 1; i <= upto; i++) ctx.lineTo(X(i), Y(sim.S[row + i]));
          ctx.strokeStyle = muted; ctx.globalAlpha = 0.12; ctx.stroke();
        } else {
          ctx.beginPath(); ctx.moveTo(X(ev), Y(sim.S[row + ev]));
          for (i = ev + 1; i <= upto; i++) ctx.lineTo(X(i), Y(sim.S[row + i]));
          ctx.strokeStyle = series; ctx.globalAlpha = 0.5; ctx.stroke();
        }
        ctx.globalAlpha = 0.9; ctx.fillStyle = warn;
        ctx.beginPath(); ctx.arc(X(ev), Y(sim.S[row + ev]), 2.2, 0, 2 * Math.PI); ctx.fill();
      }
    }
    ctx.restore();
    ctx.globalAlpha = 1;
    // strike, barrier, exercise boundary
    function hline(v, color, dash, label) {
      if (v <= lo || v >= hi) return;
      ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.setLineDash(dash);
      ctx.beginPath(); ctx.moveTo(m.l, Y(v)); ctx.lineTo(W - m.r, Y(v)); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = color; ctx.textAlign = "right"; ctx.fillText(label, W - m.r - 4, Y(v) - 5);
    }
    hline(st.K, ink, [5, 4], "strike " + st.K);
    if (st.product === "barrier") hline(st.H, warn, [], "barrier " + st.H);
    if (st.product === "american" && extras.boundary && extras.boundary.length) {
      ctx.strokeStyle = warn; ctx.lineWidth = 2; ctx.beginPath();
      var started = false;
      extras.boundary.forEach(function (b) {
        if (b[1] <= lo || b[1] >= hi) return;
        var x = m.l + b[0] / st.T * (W - m.l - m.r), y = Y(b[1]);
        if (started) ctx.lineTo(x, y); else { ctx.moveTo(x, y); started = true; }
      });
      ctx.stroke();
      ctx.fillStyle = warn; ctx.textAlign = "right";
      ctx.fillText("exercise boundary", W - m.r - 4, H - m.b - 6);
    }
    // terminal distribution (densities, so both models share one scale), revealed with the paths;
    // the other model's distribution stays behind as a ghost outline
    var x0 = W - m.r + 8, bins = extras.bins, ghost = extras.ghost, maxd = 0, b;
    for (b = 0; b < bins.length; b++) maxd = Math.max(maxd, bins[b].density, ghost ? ghost[b].density : 0);
    var reveal = Math.min(1, progress * 1.2), scale = (histW - 6) / maxd;
    for (b = 0; b < bins.length; b++) {
      var bn = bins[b], y1 = Y(bn.lo), y2 = Y(bn.hi), w = bn.density * scale * reveal;
      var itm = st.call ? bn.lo >= st.K : bn.hi <= st.K;
      ctx.fillStyle = itm ? series : line; ctx.globalAlpha = itm ? 0.75 : 1;
      ctx.fillRect(x0, y2 + 0.5, w, Math.max(y1 - y2 - 1, 0.5));
    }
    ctx.globalAlpha = 1;
    if (ghost) {
      ctx.strokeStyle = ink; ctx.globalAlpha = 0.45; ctx.lineWidth = 1.2; ctx.beginPath();
      ctx.moveTo(x0, Y(ghost[0].lo));
      for (b = 0; b < ghost.length; b++) {
        var gx = x0 + ghost[b].density * scale;
        ctx.lineTo(gx, Y(ghost[b].lo)); ctx.lineTo(gx, Y(ghost[b].hi));
      }
      ctx.lineTo(x0, Y(ghost[ghost.length - 1].hi));
      ctx.stroke(); ctx.globalAlpha = 1;
    }
    ctx.fillStyle = muted; ctx.textAlign = "left";
    ctx.fillText("at maturity", x0, m.t + 2);
    if (ghost) ctx.fillText("outline: " + extras.ghostName, x0, m.t + 15);
  }

  function histogram(values, lo, hi, nb) { // linear bins, density = share of all paths
    var bins = [], i, step = (hi - lo) / nb;
    for (i = 0; i < nb; i++) bins.push({ lo: lo + i * step, hi: lo + (i + 1) * step, count: 0, density: 0 });
    for (i = 0; i < values.length; i++) {
      var k = Math.floor((values[i] - lo) / step);
      if (k >= 0 && k < nb) bins[k].count++;
    }
    bins.forEach(function (bn) { bn.density = bn.count / values.length; });
    return bins;
  }

  function quantile(values, q) {
    var s = Array.prototype.slice.call(values).sort(function (a, b) { return a - b; });
    return s[Math.min(s.length - 1, Math.max(0, Math.floor(q * s.length)))];
  }

  // =====================================================================================  the desk
  var SLIDERS = [
    ["K", "Strike", 50, 150, 1, 100, function (v) { return fmt(v, 0); }],
    ["T", "Maturity", 0.1, 3, 0.05, 1, function (v) { return fmt(v, 2) + " y"; }],
    ["v", "Volatility", 0.05, 0.8, 0.005, 0.2, function (v) { return fmt(100 * v, 1) + " %"; }],
    ["r", "Rate", -0.01, 0.1, 0.0025, 0.04, function (v) { return fmt(100 * v, 2) + " %"; }],
    ["q", "Dividend yield", 0, 0.08, 0.0025, 0.01, function (v) { return fmt(100 * v, 2) + " %"; }],
    ["H", "Barrier", 50, 150, 1, 80, function (v) { return fmt(v, 0); }]
  ];
  var GREEKS = [["delta", "Delta", 3, "Δ"], ["gamma", "Gamma", 4, "Γ"], ["vega", "Vega", 3, "ν"], ["theta", "Theta", 4, "Θ"], ["rho", "Rho", 3, "ρ"]];

  function segmented(name, options, value, onChange) {
    var g = el("div", { "class": "lab-seg", role: "radiogroup", "aria-label": name });
    options.forEach(function (o) {
      var b = el("button", { type: "button", role: "radio", "aria-checked": String(o[0] === value), "data-v": o[0] }, o[1]);
      b.addEventListener("click", function () {
        g.querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-checked", String(x === b)); });
        onChange(o[0]);
      });
      g.appendChild(b);
    });
    return g;
  }

  function mountDesk(id) {
    var root = document.getElementById(id), data = window.PRICER_DATA || {};
    var st = { product: "barrier", call: true, model: "bs", S: 100, K: 100, T: 1, v: 0.2, r: 0.04, q: 0.01, H: 80,
      kind: "down-out", heston: data.heston || { v0: 0.0228, kappa: 3.09, theta: 0.0537, xi: 1.31, rho: -0.69 } };
    var greekView = "value", anim = null, sliders = {};
    try { // deep links: ?product=american&type=put&model=heston
      var qs = new URLSearchParams(location.search);
      if (PRODUCTS[qs.get("product")]) st.product = qs.get("product");
      if (qs.get("type") === "put") st.call = false;
      if (qs.get("model") === "heston") st.model = "heston";
      SLIDERS.forEach(function (sl) { var x = parseFloat(qs.get(sl[0])); if (isFinite(x)) st[sl[0]] = Math.min(sl[3], Math.max(sl[2], x)); });
      if (["down-out", "down-in", "up-out", "up-in"].indexOf(qs.get("kind")) >= 0) st.kind = qs.get("kind");
    } catch (e) {}
    root.textContent = "";

    var bar = el("div", { "class": "lab-bar" });
    bar.appendChild(segmented("Product", [["european", "European"], ["american", "American"], ["asian", "Asian"], ["barrier", "Barrier"]],
      st.product, function (v) { st.product = v; syncControls(); update(true); }));
    bar.appendChild(segmented("Type", [["call", "Call"], ["put", "Put"]], st.call ? "call" : "put", function (v) { st.call = v === "call"; update(true); }));
    root.appendChild(bar);

    var grid = el("div", { "class": "lab-desk" });
    var controls = el("div", { "class": "lab-controls" });
    SLIDERS.forEach(function (s) {
      var wrap = el("label", { "class": "lab-slider", "data-k": s[0] });
      var head = el("span", { "class": "lab-slider-head" });
      head.appendChild(el("span", {}, s[1]));
      var out = el("output", {}, s[6](st[s[0]]));
      head.appendChild(out);
      var inp = el("input", { type: "range", min: s[2], max: s[3], step: s[4], value: st[s[0]], "aria-label": s[1] });
      inp.addEventListener("input", function () { st[s[0]] = +inp.value; out.textContent = s[6](+inp.value); update(false); });
      wrap.appendChild(head); wrap.appendChild(inp);
      controls.appendChild(wrap);
      sliders[s[0]] = wrap;
    });
    var kindWrap = el("div", { "class": "lab-kind" });
    kindWrap.appendChild(segmented("Barrier type", [["down-out", "Down-out"], ["down-in", "Down-in"], ["up-out", "Up-out"], ["up-in", "Up-in"]],
      st.kind, function (v) { st.kind = v; update(true); }));
    controls.appendChild(kindWrap);
    grid.appendChild(controls);

    var quote = el("div", { "class": "lab-quote" });
    var priceLabel = el("div", { "class": "lab-quote-label" });
    var priceBig = el("div", { "class": "lab-price" });
    var priceSub = el("div", { "class": "lab-quote-sub" });
    quote.appendChild(priceLabel); quote.appendChild(priceBig); quote.appendChild(priceSub);
    var cards = el("div", { "class": "lab-greeks" });
    var cardEls = {};
    GREEKS.forEach(function (g) {
      var c = el("div", { "class": "lab-greek" });
      c.appendChild(el("span", { "class": "lab-greek-sym" }, g[3]));
      c.appendChild(el("span", { "class": "lab-greek-name" }, g[1]));
      var v = el("span", { "class": "lab-greek-val" });
      c.appendChild(v);
      cards.appendChild(c);
      cardEls[g[0]] = v;
    });
    quote.appendChild(cards);
    quote.appendChild(el("p", { "class": "lab-note" }, "Greeks per unit of spot, per vol point, per day and per rate point; finite differences on the pricer except for the European (closed form)."));
    grid.appendChild(quote);
    root.appendChild(grid);

    // Monte Carlo panel
    var mc = el("section", { "class": "lab-panel" });
    var mcHead = el("div", { "class": "lab-panel-head" });
    mcHead.appendChild(el("h3", {}, "Monte Carlo"));
    var modelSeg = segmented("Dynamics", [["bs", "Black-Scholes"], ["heston", "Heston (SPX-calibrated)"]], st.model, function (v) { st.model = v; runMC(); });
    mcHead.appendChild(modelSeg);
    var replay = el("button", { type: "button", "class": "lab-replay" }, "Re-run");
    replay.addEventListener("click", function () { seed += 1; runMC(); });
    mcHead.appendChild(replay);
    mc.appendChild(mcHead);
    var mcGrid = el("div", { "class": "lab-mc" });
    var canvas = el("canvas", { "class": "lab-canvas", role: "img", "aria-label": "Simulated spot paths with the distribution at maturity" });
    var conv = el("div", { "class": "lab-conv" });
    mcGrid.appendChild(canvas); mcGrid.appendChild(conv);
    mc.appendChild(mcGrid);
    var mcNote = el("p", { "class": "lab-note" });
    mc.appendChild(mcNote);
    root.appendChild(mc);

    // value and Greeks against spot
    var sp = el("section", { "class": "lab-panel" });
    var spHead = el("div", { "class": "lab-panel-head" });
    spHead.appendChild(el("h3", {}, "Across spot"));
    spHead.appendChild(segmented("Curve", [["value", "Value"], ["delta", "Delta"], ["gamma", "Gamma"], ["vega", "Vega"], ["theta", "Theta"]],
      "value", function (v) { greekView = v; drawSpot(); }));
    sp.appendChild(spHead);
    var spChart = el("div", { "class": "lab-chart" });
    sp.appendChild(spChart);
    root.appendChild(sp);

    var seed = 1;
    function syncControls() {
      sliders.H.hidden = st.product !== "barrier";
      kindWrap.hidden = st.product !== "barrier";
    }

    function update(full) {
      var g = greeks(st), h = headline(st);
      priceLabel.textContent = (st.call ? "Call" : "Put") + " · " + PRODUCTS[st.product].label +
        (st.product === "barrier" ? " " + st.kind.replace("-", "-and-") + " " + st.H : "") + " · spot 100";
      priceBig.textContent = fmt(h.price, 4);
      priceSub.textContent = PRODUCTS[st.product].method + (h.se ? " · ± " + fmt(1.96 * h.se, 4) + " (95 %, 40,000 paths)" : "");
      GREEKS.forEach(function (gk) { cardEls[gk[0]].textContent = fmt(g[gk[0]], gk[2]); });
      clearTimeout(update.t);
      update.t = setTimeout(function () { drawSpot(); runMC(); }, full ? 0 : 180);
    }

    function drawSpot() {
      var xs = [], i, base = st.S;
      for (i = 0; i <= 80; i++) xs.push(50 + i * 1.25);
      var payoff = [], cur = [], phi = st.call ? 1 : -1;
      var light = Object.assign({}, st, { treeSteps: 100 });
      xs.forEach(function (s) {
        var pt = Object.assign({}, light, { S: s });
        var y;
        if (greekView === "value") y = value(pt);
        else y = greeks(pt)[greekView];
        cur.push([s, y]);
        if (greekView === "value") {
          var pay = Math.max(phi * (s - st.K), 0);
          if (st.product === "barrier") {
            var down = st.kind.indexOf("down") === 0, hit = down ? s <= st.H : s >= st.H;
            pay = st.kind.slice(-3) === "out" ? (hit ? 0 : pay) : (hit ? pay : 0);
          }
          payoff.push([s, pay]);
        }
      });
      var names = { value: "Value today", delta: "Delta", gamma: "Gamma", vega: "Vega (per vol point)", theta: "Theta (per day)" };
      var series = [{ points: cur, cls: "lab-s1", label: names[greekView] }];
      if (greekView === "value") series.push({ points: payoff, cls: "lab-muted", dashed: true, label: st.product === "barrier" ? "Payoff if the spot stayed there" : "Payoff at maturity" });
      var refs = st.K === base ? [{ axis: "x", value: base, cls: "lab-ref lab-ref-k", label: "spot = K" }]
        : [{ axis: "x", value: base, cls: "lab-ref", label: "spot" }, { axis: "x", value: st.K, cls: "lab-ref lab-ref-k", label: "K" }];
      if (st.product === "barrier") refs.push({ axis: "x", value: st.H, cls: "lab-ref lab-ref-h", label: "H" });
      lineChart(spChart, { series: series, refLines: refs, zero: greekView !== "value", xName: "spot", xLabel: "spot",
        height: 300, xFmt: function (v) { return fmt(v, 0); }, yFmt: function (v) { return String(+v.toFixed(4)); },
        tipFmt: function (v) { return fmt(v, greekView === "gamma" || greekView === "theta" ? 4 : 3); },
        label: names[greekView] + " against spot" });
    }

    function runMC() {
      if (anim) cancelAnimationFrame(anim);
      var steps = st.product === "american" ? 50 : st.product === "asian" ? 96 : Math.max(24, Math.min(252, Math.round(252 * st.T)));
      var nPrice = st.product === "american" ? 8000 : 12000;
      var sim = simulate(st, nPrice, steps, seed);
      var res = payoffs(st, sim);
      var term = new Float64Array(nPrice);
      for (var p = 0; p < nPrice; p++) term[p] = sim.S[p * (steps + 1) + steps];
      // the other model, on fewer paths: it sets the common scale and draws the ghost distribution
      var other = Object.assign({}, st, { model: st.model === "bs" ? "heston" : "bs" });
      var simO = simulate(other, 4000, steps, seed + 101), termO = new Float64Array(4000);
      for (p = 0; p < 4000; p++) termO[p] = simO.S[p * (steps + 1) + steps];
      var lo = Math.min(quantile(term, 0.005), quantile(termO, 0.005), st.K * 0.97, st.product === "barrier" ? st.H * 0.97 : Infinity);
      var hi = Math.max(quantile(term, 0.995), quantile(termO, 0.995), st.K * 1.03, st.product === "barrier" ? st.H * 1.03 : 0);
      lo = Math.max(0, lo - 0.03 * (hi - lo)); hi = hi + 0.03 * (hi - lo);
      var bins = histogram(term, lo, hi, 40), ghost = histogram(termO, lo, hi, 40);
      // display: the first 120 paths, mixing both halves of the antithetic pairs
      var nShow = 120, showIdx = [];
      for (var k = 0; k < nShow / 2; k++) { showIdx.push(k); showIdx.push(k + nPrice / 2); }
      var dispS = new Float64Array(nShow * (steps + 1)), dispEv = new Int32Array(nShow);
      showIdx.forEach(function (src, j) {
        dispS.set(sim.S.subarray(src * (steps + 1), (src + 1) * (steps + 1)), j * (steps + 1));
        dispEv[j] = res.event[src];
      });
      var disp = { S: dispS, n: nShow, steps: steps };
      var boundary = st.product === "american" && st.model === "bs" ? exerciseBoundary(100, st.K, st.T, st.r, st.q, st.v, st.call, 200) : null;
      var extras = { lo: lo, hi: hi, bins: bins, ghost: ghost, ghostName: other.model === "bs" ? "Black-Scholes" : "Heston",
        nShow: nShow, boundary: boundary };
      var path = convergence(res.pay, 100);
      var refs, ivK, ivATM;
      if (st.model === "bs") {
        var ref = st.product === "asian" ? asianMC(100, st.K, st.T, st.r, st.q, st.v, 12, st.call, 40000, 11).price
          : value(Object.assign({}, st, { treeSteps: 600 }));
        refs = [{ axis: "y", value: ref, cls: "lab-ref lab-ref-k",
          label: { european: "closed form", american: "CRR tree", asian: "40,000 paths, control variate", barrier: "closed form" }[st.product] }];
      } else {
        // Heston's own smile at this strike: Black-Scholes at that vol carries the smile effect, and is the
        // exact Heston price for a European
        ivK = impliedVol(hestonPrice(100, st.K, st.T, st.r, st.q, st.heston, st.call), 100, st.K, st.T, st.r, st.q, st.call);
        ivATM = impliedVol(hestonPrice(100, 100, st.T, st.r, st.q, st.heston, true), 100, 100, st.T, st.r, st.q, true);
        refs = [
          { axis: "y", value: value(Object.assign({}, st, { treeSteps: 600 })), cls: "lab-ref",
            label: "Black-Scholes, " + fmt(100 * st.v, 1) + " % (slider)" },
          { axis: "y", value: value(Object.assign({}, st, { v: ivK, treeSteps: 600 })), cls: "lab-ref lab-ref-k",
            label: st.product === "european" ? "Heston, Fourier" : "Black-Scholes at Heston's vol for K, " + fmt(100 * ivK, 1) + " %" }
        ];
      }
      var t0 = null, dur = reduceMotion ? 0 : 1600;
      function frame(ts) {
        if (t0 === null) t0 = ts;
        var prog = dur ? Math.min(1, (ts - t0) / dur) : 1;
        drawPaths(canvas, st, disp, dispEv, term, prog, extras);
        var upto = Math.max(1, Math.round(prog * path.length));
        var shown = path.slice(0, upto);
        lineChart(conv, {
          height: 300, series: [{ points: shown.map(function (c) { return [c[0], c[1]]; }), cls: "lab-s1", label: "estimate" }],
          band: shown.map(function (c) { return [c[0], c[1] - 1.96 * c[2], c[1] + 1.96 * c[2]]; }),
          refLines: refs,
          xName: "paths", xLabel: "paths", xFmt: function (v) { return v >= 1000 ? fmt(v / 1000, 0) + "k" : fmt(v, 0); },
          yFmt: function (v) { return String(+v.toFixed(3)); }, label: "Running Monte Carlo estimate"
        });
        if (prog < 1) anim = requestAnimationFrame(frame);
      }
      anim = requestAnimationFrame(frame);
      var last = path[path.length - 1];
      var what = { european: "European", american: "Longstaff-Schwartz (cubic basis, 50 exercise dates, rule fitted on independent paths)", asian: "arithmetic Asian, 12 fixings",
        barrier: "barrier, " + (st.T * 252 < 24 ? "" : "daily ") + "monitored on the simulation grid" }[st.product];
      mcNote.textContent = (st.model === "heston"
        ? "Heston with the parameters calibrated on SPX (v0 " + fmt(st.heston.v0, 4) + ", κ " + fmt(st.heston.kappa, 2) + ", θ " + fmt(st.heston.theta, 4) +
          ", ξ " + fmt(st.heston.xi, 2) + ", ρ " + fmt(st.heston.rho, 2) + "). Its smile gives a vol of " + fmt(100 * ivK, 1) + " % at K = " + st.K +
          " (" + fmt(100 * ivATM, 1) + " % at K = 100) against " + fmt(100 * st.v, 1) + " % on the slider: Black-Scholes at that vol carries the smile, " +
          "and what remains between it and the estimate is the dynamics. "
        : "") + "Estimate " + fmt(last[1], 4) + " ± " + fmt(1.96 * last[2], 4) + " (95 %) with " + last[0].toLocaleString("en") + " antithetic paths, " + what + ". " +
        (st.product === "barrier" ? "Orange dots: barrier hits." : st.product === "american" ? "Orange dots: exercise under the regression rule." : "");
    }

    window.addEventListener("themechange", runMC);
    if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", runMC);
    var lastWidth = 0, ro = window.ResizeObserver ? new ResizeObserver(function () {
      var w = Math.round(canvas.getBoundingClientRect().width);
      if (w !== lastWidth) { lastWidth = w; drawSpot(); runMC(); }
    }) : null;
    if (ro) ro.observe(canvas);
    syncControls();
    update(true);
  }

  // =====================================================================================  SPX results
  var MODEL_CLS = ["lab-m0", "lab-m1", "lab-m2", "lab-m3"];

  function onWidth(node, draw) { // draw now, and again when the width changes
    var last = -1;
    function go() { var w = Math.round(node.clientWidth); if (w !== last) { last = w; draw(); } }
    go();
    if (window.ResizeObserver) new ResizeObserver(go).observe(node);
  }

  function mountRisk(id) {
    var root = document.getElementById(id);
    if (!window.PRICER_DATA || !root) return;
    onWidth(root, function () { drawRisk(root, window.PRICER_DATA); });
  }

  function drawRisk(root, data) {
    root.textContent = "";
    var legend = el("div", { "class": "lab-legend" });
    data.models.forEach(function (m, i) {
      var it = el("span", { "class": "lab-legend-item" });
      it.appendChild(el("span", { "class": "lab-swatch " + MODEL_CLS[i] }));
      it.appendChild(document.createTextNode(m));
      legend.appendChild(it);
    });
    root.appendChild(legend);
    // dot plot: price relative to SLV
    var W = Math.max(300, Math.round(root.clientWidth)), narrow = W < 520;
    var slv = data.models.length - 1, rows = data.rows, rowH = narrow ? 58 : 46;
    var m = narrow ? { l: 8, r: 14, t: 22, b: 34 } : { l: 210, r: 20, t: 10, b: 34 };
    var H = m.t + m.b + rowH * rows.length, rel = [];
    rows.forEach(function (r) { r.prices.forEach(function (p) { rel.push(100 * (p / r.prices[slv] - 1)); }); });
    var lo = Math.min.apply(null, rel.concat([0])), hi = Math.max.apply(null, rel.concat([0])), pad = (hi - lo) * 0.06;
    lo -= pad; hi += pad;
    function X(v) { return m.l + (v - lo) / (hi - lo) * (W - m.l - m.r); }
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, "class": "lab-svg", role: "img", "aria-label": "Exotic prices under four models, relative to SLV" });
    niceTicks(lo, hi, 6).forEach(function (v) {
      s.appendChild(svg("line", { x1: X(v), x2: X(v), y1: m.t, y2: H - m.b, "class": Math.abs(v) < 1e-9 ? "lab-zero" : "lab-grid" }));
      s.appendChild(svg("text", { x: X(v), y: H - m.b + 16, "class": "lab-tick", "text-anchor": "middle" }, (v > 0 ? "+" : "") + v + " %"));
    });
    s.appendChild(svg("text", { x: W - m.r, y: H - 3, "class": "lab-tick", "text-anchor": "end" }, "price relative to SLV"));
    var tipHost = root;
    var tip = el("div", { "class": "lab-tip", hidden: "" });
    rows.forEach(function (r, k) {
      var y = m.t + rowH * (k + 0.5);
      var name = r.product.split(",")[0], spec = r.product.split(",").slice(1).join(",").trim();
      if (narrow) { // labels above the row on a phone
        s.appendChild(svg("text", { x: m.l, y: y - 14, "class": "lab-row-label" }, name + " · " + spec));
      } else {
        s.appendChild(svg("text", { x: 0, y: y - 2, "class": "lab-row-label" }, name));
        s.appendChild(svg("text", { x: 0, y: y + 13, "class": "lab-tick" }, spec));
      }
      var xs = r.prices.map(function (p) { return X(100 * (p / r.prices[slv] - 1)); });
      s.appendChild(svg("line", { x1: Math.min.apply(null, xs), x2: Math.max.apply(null, xs), y1: y, y2: y, "class": "lab-range" }));
      r.prices.forEach(function (p, i) {
        var g = svg("g", { "class": "lab-pt", tabindex: "0" });
        g.appendChild(svg("circle", { cx: xs[i], cy: y, r: 12, "class": "lab-hit" }));
        g.appendChild(svg("circle", { cx: xs[i], cy: y, r: 5.5, "class": "lab-dot " + MODEL_CLS[i] }));
        var text = data.models[i] + ": " + fmt(p, 3) + " % of spot (" + fmt(100 * (p / r.prices[slv] - 1), 1) + " % vs SLV)";
        g.addEventListener("pointerenter", function (e) { show(text, e); });
        g.addEventListener("focus", function () { show(text, null, xs[i]); });
        g.addEventListener("pointerleave", hide); g.addEventListener("blur", hide);
        s.appendChild(g);
      });
    });
    function show(text, e, xsvg) {
      tip.textContent = text; tip.hidden = false;
      var rect = s.getBoundingClientRect(), hr = tipHost.getBoundingClientRect();
      var px = e ? e.clientX - hr.left : xsvg / W * rect.width;
      tip.style.left = Math.min(Math.max(px + 10, 0), hr.width - tip.offsetWidth) + "px";
      tip.style.top = (e ? e.clientY - hr.top + 12 : rect.top - hr.top) + "px";
    }
    function hide() { tip.hidden = true; }
    var wrap = el("div", { "class": "lab-chart" });
    wrap.appendChild(s);
    root.appendChild(wrap);
    root.appendChild(tip);
  }

  function mountMix(id) {
    var data = window.PRICER_DATA, root = document.getElementById(id);
    if (!data || !root || !data.mix_sweep) return;
    root.textContent = "";
    var charts = [["dao", "Down-and-out call, H 80 %"], ["uao", "Up-and-out call, H 120 %"]].map(function (c) {
      var box = el("div", { "class": "lab-chart lab-small" });
      box.appendChild(el("div", { "class": "lab-chart-title" }, c[1]));
      var host = el("div", { "class": "lab-chart" });
      box.appendChild(host);
      root.appendChild(box);
      return [c, host];
    });
    // both boxes exist before drawing, so each chart is drawn at its final width
    charts.forEach(function (ch) { onWidth(ch[1], function () { drawMix(ch[1], ch[0], data); }); });
  }

  function drawMix(host, c, data) {
    {
      var pts = data.mix_sweep.map(function (s) { return [s.mix, s[c[0]]]; });
      lineChart(host, {
        height: 210, series: [{ points: pts, cls: "lab-m3", label: "SLV price" }], bandCls: "lab-band-m3",
        band: data.mix_sweep.map(function (s) { return [s.mix, s[c[0]] - 2 * s[c[0] + "_se"], s[c[0]] + 2 * s[c[0] + "_se"]]; }),
        xName: "mix", xLabel: "mixing fraction", xFmt: function (v) { return String(+v.toFixed(2)); },
        yFmt: function (v) { return String(+v.toFixed(2)); }, tipFmt: function (v) { return fmt(v, 3) + " %"; }, label: c[1]
      });
    }
  }

  function mountErrors(id) {
    var data = window.PRICER_DATA, root = document.getElementById(id);
    if (!data || !root || !data.errors) return;
    var t = el("table", { "class": "lab-table" }), tr = el("tr");
    ["Expiry", "Half bid-ask", "SVI", "Heston", "Local vol", "SLV"].forEach(function (h, i) { tr.appendChild(el("th", { scope: "col", "class": i ? "num" : "" }, h)); });
    t.appendChild(el("thead")).appendChild(tr);
    var body = el("tbody");
    data.errors.forEach(function (e) {
      var row = el("tr");
      row.appendChild(el("th", { scope: "row" }, e.expiry));
      [fmt(e.half_spread, 1), fmt(e.svi, 1), fmt(e.heston, 0), fmt(e.lv, 1), fmt(e.slv, 1)].forEach(function (v) { row.appendChild(el("td", { "class": "num" }, v)); });
      body.appendChild(row);
    });
    t.appendChild(body);
    root.appendChild(t);
  }

  window.PricerWidget = {
    mountDesk: mountDesk, mountRisk: mountRisk, mountMix: mountMix, mountErrors: mountErrors,
    bs: bs, barrier: barrier, crr: crr, american: american, exerciseBoundary: exerciseBoundary, geoAsian: geoAsian,
    levyAsian: levyAsian, asianMC: asianMC, hestonPrice: hestonPrice, impliedVol: impliedVol, simulate: simulate, payoffs: payoffs, convergence: convergence, greeks: greeks
  };
})();
