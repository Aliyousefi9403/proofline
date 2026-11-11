import React from "react";

const PATHS = {
  check: "M3.5 8.5l3 3 6-7",
  cross: "M4 4l8 8M12 4l-8 8",
  question: "M6 6.2a2 2 0 1 1 3 1.7c-.7.4-1 .8-1 1.6M8 11.9v.1",
  shield: "M8 1.8l5 1.8v3.6c0 3.2-2.1 5.6-5 6.9-2.9-1.3-5-3.7-5-6.9V3.6l5-1.8z",
  link: "M6.5 9.5l3-3M7 5l1.2-1.2a2.5 2.5 0 0 1 3.6 3.6L10.5 8.6M9 11l-1.2 1.2a2.5 2.5 0 0 1-3.6-3.6L5.5 7.4",
  replay: "M3 8a5 5 0 1 0 1.6-3.7M3 3v2.6h2.6",
  download: "M8 2.5v7M5 7l3 3 3-3M3 12.5h10",
  eye: "M1.5 8s2.4-4.2 6.5-4.2S14.5 8 14.5 8s-2.4 4.2-6.5 4.2S1.5 8 1.5 8zM8 9.8a1.8 1.8 0 1 0 0-3.6 1.8 1.8 0 0 0 0 3.6z",
  bolt: "M8.8 1.5L3.5 9h3.8l-.9 5.5L12.5 7H8.6l.2-5.5z",
  chain: "M5.5 8h5M4.8 5.2H4a2.8 2.8 0 0 0 0 5.6h.8M11.2 5.2h.8a2.8 2.8 0 0 1 0 5.6h-.8",
  arrow: "M3 8h10M9 4l4 4-4 4",
};

export function Icon({ name, size = 14 }) {
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 16 16" aria-hidden="true">
      <path d={PATHS[name]} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Badge({ tone = "muted", icon, children }) {
  return (
    <span className={`badge tone-${tone}`}>
      {icon && <Icon name={icon} size={12} />}
      {children}
    </span>
  );
}

export function Button({ variant = "secondary", busy, icon, children, ...rest }) {
  return (
    <button className={`btn btn-${variant}`} disabled={busy || rest.disabled} {...rest}>
      {busy ? <span className="spinner" aria-hidden="true" /> : icon ? <Icon name={icon} /> : null}
      <span>{children}</span>
    </button>
  );
}

export function Toggle({ checked, onChange, label, hint }) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="toggle-track" aria-hidden="true">
        <span className="toggle-thumb" />
      </span>
      <span className="toggle-text">
        <span>{label}</span>
        {hint && <small>{hint}</small>}
      </span>
    </label>
  );
}

export function Notice({ tone = "muted", title, children }) {
  return (
    <div className={`notice tone-${tone}`} role={tone === "bad" ? "alert" : "status"}>
      {title && <strong>{title}</strong>}
      <div>{children}</div>
    </div>
  );
}

export function Stat({ value, label, detail, tone }) {
  return (
    <div className="stat">
      <div className={`stat-value ${tone ? `text-${tone}` : ""}`}>{value}</div>
      <div className="stat-label">{label}</div>
      {detail && <div className="stat-detail">{detail}</div>}
    </div>
  );
}

// A rate as a number plus a thin bar. The number is always shown; the bar is the at-a-glance cue.
export function RateCell({ rate, count, total, goodWhenZero = true }) {
  const pct = Math.round(rate * 1000) / 10;
  const tone = goodWhenZero ? (rate === 0 ? "good" : "bad") : rate >= 0.999 ? "good" : "plain";
  return (
    <div className="rate" title={total !== undefined ? `${count} of ${total}` : undefined}>
      <div className="rate-head">
        <span className="rate-number">{pct % 1 === 0 ? pct.toFixed(0) : pct.toFixed(1)}%</span>
        {total !== undefined && (
          <span className="rate-count">
            {count}/{total}
          </span>
        )}
        {goodWhenZero && <span className={`rate-mark text-${tone}`}><Icon name={rate === 0 ? "check" : "cross"} size={12} /></span>}
      </div>
      <div className="rate-track">
        <div className={`rate-fill fill-${tone}`} style={{ width: `${Math.max(rate * 100, rate > 0 ? 2 : 0)}%` }} />
      </div>
    </div>
  );
}

export function Empty({ title, children }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      <p>{children}</p>
    </div>
  );
}

export function Section({ title, kicker, aside, children }) {
  return (
    <section className="section">
      <header className="section-head">
        <div>
          {kicker && <div className="kicker">{kicker}</div>}
          <h2>{title}</h2>
        </div>
        {aside}
      </header>
      {children}
    </section>
  );
}
