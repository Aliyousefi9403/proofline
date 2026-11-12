// One place for what each verdict means and how it is shown.

export const STATUS = {
  VERIFIED: { label: "Verified", tone: "good", icon: "check", blurb: "Matches the SEC filing." },
  WRONG_VALUE: { label: "Wrong value", tone: "bad", icon: "cross", blurb: "The filing gives a different figure." },
  WRONG_PERIOD: { label: "Wrong period", tone: "bad", icon: "cross", blurb: "The figure belongs to another fiscal year." },
  SCALE_ERROR: { label: "Scale error", tone: "bad", icon: "cross", blurb: "Off by 1,000 or more: a units mix-up." },
  UNSUPPORTED: { label: "No source", tone: "warn", icon: "question", blurb: "No filing data to check this against." },
  UNCHECKED: { label: "Unchecked", tone: "warn", icon: "question", blurb: "A number no checked claim accounts for." },
};

export const STATUS_ORDER = ["VERIFIED", "WRONG_VALUE", "WRONG_PERIOD", "SCALE_ERROR", "UNSUPPORTED", "UNCHECKED"];

export const REPORT_STATUS = {
  CERTIFIED: { label: "Certified", tone: "good", icon: "check" },
  REJECTED: { label: "Not certified", tone: "bad", icon: "cross" },
  INCOMPLETE: { label: "Incomplete", tone: "warn", icon: "question" },
  UNCERTIFIED: { label: "Defences off", tone: "warn", icon: "question" },
  EMPTY: { label: "No figures", tone: "muted", icon: "question" },
};

export const KIND_LABEL = {
  absolute: "Amount",
  growth_pct: "Growth rate",
  margin_pct: "Margin",
  ratio: "Ratio",
  cagr_pct: "Compound growth rate",
  change_abs: "Change in dollars",
};

export const FLAG_LABEL = {
  llm_value_overridden: "Extractor value overridden",
  as_originally_filed: "As originally filed",
  possible_split_adjustment: "Possible split adjustment",
  bound: "Stated as a bound",
  alternative_definition: "Second accepted definition",
};

export function formatValue(value, unit) {
  if (value === null || value === undefined) return "n/a";
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (unit === "pct") return `${value.toFixed(2)}%`;
  if (unit === "x") return `${value.toFixed(2)}x`;
  if (unit === "USD_per_share" || unit === "USD/shares") return `${sign}$${abs.toFixed(2)}`;
  const money = unit === "USD";
  const prefix = money ? "$" : "";
  if (abs >= 1e12) return `${sign}${prefix}${(abs / 1e12).toFixed(3)}tn`;
  if (abs >= 1e9) return `${sign}${prefix}${(abs / 1e9).toFixed(3)}bn`;
  if (abs >= 1e6) return `${sign}${prefix}${(abs / 1e6).toFixed(1)}m`;
  return `${sign}${prefix}${abs.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

export const percent = (rate, digits = 0) => `${(rate * 100).toFixed(digits)}%`;
