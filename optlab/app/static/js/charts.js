// Small SVG charts with axes, units and hover values. Colours come from the CSS tokens.

const NS = "http://www.w3.org/2000/svg";

function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) el.setAttribute(k, v);
  for (const c of children) if (c) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}

function ticks(lo, hi, n = 5) {
  const span = hi - lo || 1;
  const step0 = span / n;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= step0) || step0;
  const out = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi + 1e-12; t += step) out.push(+t.toFixed(10));
  return out;
}

// Bars: data = [{label, value, title}]
export function barChart(data, { height = 200, unit = "", yFormat = (v) => v.toFixed(0), label = "Bar chart" } = {}) {
  const W = 720, H = height, L = 44, R = 10, T = 14, B = 34;
  const vmax = Math.max(1e-9, ...data.map((d) => d.value));
  const yt = ticks(0, vmax, 4);
  const top = Math.max(vmax, yt[yt.length - 1]);
  const y = (v) => T + (H - T - B) * (1 - v / top);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": label });
  for (const t of yt) {
    svg.append(s("line", { x1: L, x2: W - R, y1: y(t), y2: y(t), class: "grid" }));
    svg.append(s("text", { x: L - 6, y: y(t) + 4, "text-anchor": "end" }, yFormat(t) + unit));
  }
  const bw = (W - L - R) / Math.max(1, data.length);
  data.forEach((d, i) => {
    const x = L + i * bw + bw * 0.18;
    const g = s("g");
    g.append(s("rect", { x, y: y(d.value), width: bw * 0.64, height: Math.max(0, y(0) - y(d.value)), rx: 2,
      fill: "var(--chart-1)" }, s("title", {}, d.title || `${d.label}: ${yFormat(d.value)}${unit}`)));
    if (data.length <= 24 && d.value > 0)
      g.append(s("text", { x: x + bw * 0.32, y: y(d.value) - 4, "text-anchor": "middle" }, yFormat(d.value)));
    if (data.length <= 24 || i % Math.ceil(data.length / 12) === 0)
      g.append(s("text", { x: x + bw * 0.32, y: H - B + 16, "text-anchor": "middle" }, d.label));
    svg.append(g);
  });
  svg.append(s("line", { x1: L, x2: W - R, y1: y(0), y2: y(0), class: "axis" }));
  return svg;
}

// Lines: series = [{name, points: [[x, y]], color}]
export function lineChart(series, { height = 240, xFormat = (v) => v.toFixed(1), yFormat = (v) => v.toFixed(1),
  xLabel = "", yLabel = "", label = "Line chart" } = {}) {
  const W = 720, H = height, L = 52, R = 14, T = 14, B = 40;
  const xs = series.flatMap((ser) => ser.points.map((p) => p[0]));
  const ys = series.flatMap((ser) => ser.points.map((p) => p[1]));
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  let [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  const pad = (y1 - y0) * 0.08 || Math.abs(y1) * 0.05 || 1;
  y0 -= pad; y1 += pad;
  const X = (v) => L + (W - L - R) * (v - x0) / (x1 - x0 || 1);
  const Y = (v) => T + (H - T - B) * (1 - (v - y0) / (y1 - y0 || 1));
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": label });
  for (const t of ticks(y0, y1, 5)) {
    svg.append(s("line", { x1: L, x2: W - R, y1: Y(t), y2: Y(t), class: "grid" }));
    svg.append(s("text", { x: L - 6, y: Y(t) + 4, "text-anchor": "end" }, yFormat(t)));
  }
  for (const t of ticks(x0, x1, 6)) {
    svg.append(s("text", { x: X(t), y: H - B + 16, "text-anchor": "middle" }, xFormat(t)));
  }
  if (xLabel) svg.append(s("text", { x: (L + W - R) / 2, y: H - 6, "text-anchor": "middle" }, xLabel));
  if (yLabel) svg.append(s("text", { x: 12, y: T + 8 }, yLabel));
  series.forEach((ser, k) => {
    const color = ser.color || `var(--chart-${(k % 4) + 1})`;
    const d = ser.points.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
    svg.append(s("path", { d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round" }));
    for (const p of ser.points.filter((_, i) => ser.markers || i % Math.ceil(ser.points.length / 30) === 0)) {
      svg.append(s("circle", { cx: X(p[0]), cy: Y(p[1]), r: ser.markers ? 3.2 : 6, fill: ser.markers ? color : "transparent" },
        s("title", {}, `${ser.name}: ${xFormat(p[0])} → ${yFormat(p[1])}`)));
    }
  });
  svg.append(s("line", { x1: L, x2: L, y1: T, y2: H - B, class: "axis" }));
  svg.append(s("line", { x1: L, x2: W - R, y1: H - B, y2: H - B, class: "axis" }));
  return svg;
}

export function legend(series) {
  const el = document.createElement("div");
  el.className = "legend";
  series.forEach((ser, k) => {
    const item = document.createElement("span");
    const sw = document.createElement("i");
    sw.style.background = ser.color || `var(--chart-${(k % 4) + 1})`;
    item.append(sw, ser.name);
    el.append(item);
  });
  return el;
}
