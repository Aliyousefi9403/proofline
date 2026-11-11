import React from "react";
import { FLAG_LABEL, KIND_LABEL, STATUS, formatValue } from "../status.js";
import { Badge, Icon } from "./ui.jsx";

function Row({ label, children }) {
  return (
    <div className="kv">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function Source({ fact, metricLabels }) {
  const unit = fact.unit === "USD/shares" ? "USD_per_share" : fact.unit;
  const filings = fact.reported ? fact.reported.length : 0;
  return (
    <li className="source">
      <div className="source-top">
        <span>
          {fact.ticker} {metricLabels[fact.metric] || fact.metric}, FY{fact.fiscal_year}
        </span>
        <strong>{formatValue(fact.value, unit)}</strong>
      </div>
      <div className="source-meta">
        {fact.derived ? `Derived: ${fact.formula}. ` : ""}
        Period {fact.start ? `${fact.start} to ` : "as of "}
        {fact.end}. {fact.form} filed {fact.filed}.
        {filings > 1 ? ` Stated in ${filings} filings.` : ""}
      </div>
      <a className="source-link" href={fact.url} target="_blank" rel="noreferrer">
        <Icon name="link" size={12} /> SEC filing {fact.accn}
      </a>
    </li>
  );
}

export default function DetailPanel({ item, metricLabels, onClose }) {
  if (!item) {
    return (
      <aside className="detail detail-empty">
        <Icon name="eye" size={18} />
        <p>Select a highlighted number to see the claim, the filed figure, the calculation and the filing.</p>
      </aside>
    );
  }
  const meta = STATUS[item.status];
  if (item.kind === "unchecked") {
    const u = item.data;
    return (
      <aside className="detail">
        <header className="detail-head">
          <Badge tone={meta.tone} icon={meta.icon}>{meta.label}</Badge>
          <button className="icon-btn" onClick={onClose} aria-label="Close details"><Icon name="cross" /></button>
        </header>
        <div className="detail-figure">{u.token.text}</div>
        <p className="detail-reason">{u.reason}</p>
        <dl>
          <Row label="Read as">{u.value === null ? "Could not be parsed" : formatValue(u.value, u.unit === "USD" ? "USD" : u.unit)}</Row>
          <Row label="Why it matters">
            The coverage check found this number in the text, but no checked claim covers it. That is how a skipped or
            disguised figure is caught.
          </Row>
        </dl>
      </aside>
    );
  }
  const v = item.data;
  const claim = v.claim;
  return (
    <aside className="detail">
      <header className="detail-head">
        <Badge tone={meta.tone} icon={meta.icon}>{meta.label}</Badge>
        <button className="icon-btn" onClick={onClose} aria-label="Close details"><Icon name="cross" /></button>
      </header>
      <div className="detail-figure">{v.token ? v.token.text : claim.span}</div>
      <p className="detail-reason">{v.reason}</p>
      {v.hint && <p className="detail-hint">{v.hint}</p>}
      {v.notes && v.notes.map((note) => <p className="detail-hint" key={note}>{note}</p>)}

      <div className="compare">
        <div>
          <span>Report says</span>
          <strong>{v.claimed_display}</strong>
        </div>
        <div>
          <span>Filings say</span>
          <strong>{v.expected_display || "No data"}</strong>
        </div>
      </div>

      <h3>Claim</h3>
      <dl>
        <Row label="Company">{claim.company}{claim.compare_company ? ` vs ${claim.compare_company}` : ""}</Row>
        <Row label="Metric">
          {metricLabels[claim.metric] || claim.metric}
          {claim.denominator ? ` / ${metricLabels[claim.denominator] || claim.denominator}` : ""}
        </Row>
        <Row label="Type">{KIND_LABEL[claim.kind] || claim.kind}</Row>
        <Row label="Fiscal year">
          {claim.fiscal_year ?? "Not stated"}
          {claim.compare_year ? ` (against ${claim.compare_year})` : ""}
        </Row>
        {v.flags.length > 0 && (
          <Row label="Flags">{v.flags.map((f) => FLAG_LABEL[f] || f).join(", ")}</Row>
        )}
      </dl>

      {v.calculation && (
        <>
          <h3>Calculation</h3>
          <pre className="calc">{v.calculation}</pre>
        </>
      )}

      {v.sources.length > 0 && (
        <>
          <h3>Source</h3>
          <ul className="sources">
            {v.sources.map((fact) => (
              <Source key={`${fact.ticker}-${fact.metric}-${fact.fiscal_year}`} fact={fact} metricLabels={metricLabels} />
            ))}
          </ul>
        </>
      )}
    </aside>
  );
}
