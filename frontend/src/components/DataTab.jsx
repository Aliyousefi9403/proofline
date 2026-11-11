import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { formatValue } from "../status.js";
import { Empty, Notice, Section } from "./ui.jsx";

export default function DataTab({ meta }) {
  const companies = meta?.companies || [];
  const [ticker, setTicker] = useState("AAPL");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setData(null);
    api.facts(ticker).then(setData).catch((e) => setError(e.message));
  }, [ticker]);

  return (
    <Section
      kicker="Source data"
      title="What the checker checks against"
      aside={
        <div className="segmented" role="tablist" aria-label="Company">
          {companies.map((c) => (
            <button key={c.ticker} role="tab" aria-selected={c.ticker === ticker}
              className={c.ticker === ticker ? "is-on" : ""} onClick={() => setTicker(c.ticker)}>
              {c.ticker}
            </button>
          ))}
        </div>
      }
    >
      <p className="lede">
        Annual figures from each company's 10-K filings, fetched once from SEC EDGAR and cached locally. Each value
        links to the filing it comes from. Later filings restate earlier years; the latest filed value is used.
      </p>
      {error && <Notice tone="bad">{error}</Notice>}
      {!data && !error && <Empty title="Loading">Reading cached filing data.</Empty>}
      {data && (
        <div className="table-wrap">
          <table className="table table-compact">
            <thead>
              <tr>
                <th>{data.name}</th>
                {data.years.map((y) => <th key={y} className="num-col">FY{y}</th>)}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row) => (
                <tr key={row.metric}>
                  <td>{row.label}</td>
                  {data.years.map((y) => {
                    const cell = row.values[String(y)];
                    return (
                      <td key={y} className="num-col">
                        {cell ? (
                          <a href={cell.url} target="_blank" rel="noreferrer"
                            title={`${cell.derived ? "Derived. " : ""}Filed ${cell.filed}, accession ${cell.accn}`}>
                            {formatValue(cell.value, row.unit)}{cell.derived ? "*" : ""}
                          </a>
                        ) : <span className="muted">n/a</span>}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="fineprint">* Derived from two filed figures (for example revenue minus cost of revenue). n/a: the company does not report this line.</p>
    </Section>
  );
}
