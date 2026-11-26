// A model's fitted parameters as a person reads them: the raw numbers the API sends (SVI per maturity, SSVI, Heston),
// with the quantities a quant checks them by (ATM vol, long-run vol, Feller). Pure functions, tested.

type Dict = Record<string, unknown>;

const num = (x: unknown): number | null => (typeof x === "number" && Number.isFinite(x) ? x : null);
const nums = (x: unknown): number[] => (Array.isArray(x) ? x.map((v) => (typeof v === "number" ? v : Number.NaN)) : []);

/** Raw SVI of the total variance, w(k) = a + b (rho (k - m) + sqrt((k - m)^2 + sigma^2)), per maturity. */
export type SviRow = { T: number; a: number; b: number; rho: number; m: number; sigma: number; atmVol: number | null };

export function sviRows(p: Dict | null | undefined): SviRow[] {
  const Ts = nums(p?.maturities);
  const rows = Array.isArray(p?.params) ? (p.params as unknown[]) : [];
  return Ts.flatMap((T, i) => {
    const [a, b, rho, m, sigma] = nums(rows[i]);
    if ([a, b, rho, m, sigma].some((v) => v === undefined || Number.isNaN(v))) return [];
    const w0 =
      (a as number) + (b as number) * (-(rho as number) * (m as number) + Math.hypot(m as number, sigma as number));
    return [
      {
        T,
        a: a as number,
        b: b as number,
        rho: rho as number,
        m: m as number,
        sigma: sigma as number,
        atmVol: w0 > 0 && T > 0 ? Math.sqrt(w0 / T) : null,
      },
    ];
  });
}

/** SSVI by maturity: theta the ATM total variance, psi its curvature, rho its skew. */
export type SsviRow = { T: number; theta: number; psi: number; rho: number; atmVol: number | null };

export function ssviRows(p: Dict | null | undefined): SsviRow[] {
  const Ts = nums(p?.maturities);
  const thetas = nums(p?.thetas);
  const psis = nums(p?.psis);
  const rhos = nums(p?.rhos);
  return Ts.flatMap((T, i) => {
    const theta = thetas[i];
    const psi = psis[i];
    const rho = rhos[i];
    if (theta === undefined || psi === undefined || rho === undefined) return [];
    return [{ T, theta, psi, rho, atmVol: theta > 0 && T > 0 ? Math.sqrt(theta / T) : null }];
  });
}

/** Heston's five parameters and what they mean: today's vol, the long-run vol, the half-life of a shock, Feller. */
export type HestonView = {
  v0: number;
  kappa: number;
  theta: number;
  xi: number;
  rho: number;
  spotVol: number;
  longRunVol: number;
  halfLife: number | null;
  feller: number | null;
};

export function hestonView(p: Dict | null | undefined): HestonView | null {
  const v0 = num(p?.v0);
  const kappa = num(p?.kappa);
  const theta = num(p?.theta);
  const xi = num(p?.xi);
  const rho = num(p?.rho);
  if (v0 === null || kappa === null || theta === null || xi === null || rho === null) return null;
  return {
    v0,
    kappa,
    theta,
    xi,
    rho,
    spotVol: Math.sqrt(Math.max(v0, 0)),
    longRunVol: Math.sqrt(Math.max(theta, 0)),
    halfLife: kappa > 0 ? Math.LN2 / kappa : null,
    feller: xi > 0 ? (2 * kappa * theta) / (xi * xi) : null,
  };
}
