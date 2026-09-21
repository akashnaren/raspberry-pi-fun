/** Local unit conversion. Currency rates are demo figures, not live FX. */

export const DEMO_FX_NOTE = "demo rates, not live FX";

export const LENGTH_TO_M = {
  mm: 0.001,
  cm: 0.01,
  m: 1,
  km: 1000,
  in: 0.0254,
  ft: 0.3048,
  mi: 1609.344,
};

export const MASS_TO_KG = {
  g: 0.001,
  kg: 1,
  oz: 0.028349523125,
  lb: 0.45359237,
};

/** How many of this currency equal 1 USD. Hard-coded demo rates. */
export const DEMO_FX = {
  USD: 1,
  EUR: 0.92,
  GBP: 0.79,
  JPY: 149,
};

export const UNIT_LABELS = {
  mm: "mm",
  cm: "cm",
  m: "m",
  km: "km",
  in: "in",
  ft: "ft",
  mi: "mi",
  g: "g",
  kg: "kg",
  oz: "oz",
  lb: "lb",
  C: "°C",
  F: "°F",
  K: "K",
  USD: "USD",
  EUR: "EUR",
  GBP: "GBP",
  JPY: "JPY",
};

function familyOf(unit) {
  if (Object.prototype.hasOwnProperty.call(LENGTH_TO_M, unit)) return "length";
  if (Object.prototype.hasOwnProperty.call(MASS_TO_KG, unit)) return "mass";
  if (unit === "C" || unit === "F" || unit === "K") return "temp";
  if (Object.prototype.hasOwnProperty.call(DEMO_FX, unit)) return "currency";
  return "";
}

function toCelsius(value, unit) {
  if (unit === "C") return value;
  if (unit === "F") return ((value - 32) * 5) / 9;
  return value - 273.15;
}

function fromCelsius(celsius, unit) {
  if (unit === "C") return celsius;
  if (unit === "F") return (celsius * 9) / 5 + 32;
  return celsius + 273.15;
}

export function formatAmount(n) {
  if (!Number.isFinite(n)) return "";
  const abs = Math.abs(n);
  const digits = abs >= 1000 ? 2 : abs >= 100 ? 3 : abs >= 1 ? 4 : 6;
  return String(Number(n.toFixed(digits)));
}

export function convert(value, from, to) {
  const n = Number(value);
  const src = String(from || "");
  const dst = String(to || "");
  const family = familyOf(src);
  if (!Number.isFinite(n) || !family || family !== familyOf(dst)) {
    return { ok: false, error: "Those units don't share a measure." };
  }
  let result = n;
  let note = "";
  if (family === "length") result = (n * LENGTH_TO_M[src]) / LENGTH_TO_M[dst];
  else if (family === "mass") result = (n * MASS_TO_KG[src]) / MASS_TO_KG[dst];
  else if (family === "temp") result = fromCelsius(toCelsius(n, src), dst);
  else {
    result = (n / DEMO_FX[src]) * DEMO_FX[dst];
    note = DEMO_FX_NOTE;
  }
  return {
    ok: true,
    result,
    text: formatAmount(result),
    from: src,
    to: dst,
    labelFrom: UNIT_LABELS[src],
    labelTo: UNIT_LABELS[dst],
    family,
    note,
  };
}
