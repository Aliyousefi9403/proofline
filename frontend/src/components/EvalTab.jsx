import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { percent } from "../status.js";
import { Empty, Notice, RateCell, Section, Stat } from "./ui.jsx";

export default function EvalTab() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { api.results("eval").then(setData).catch((e) => setError(e.message)); }, []);

  if (error) return <Notice tone="bad">{error}</Notice>;
  if (!data) return <Empty title="Loading">Reading evaluation results.</Empty>;
  if (!data.available) {
    return <Empty title="No evaluation yet">Run <code>uv run python eval/run_eval.py</code> to generate it.</Empty>;
  }
  const fa = data.false_alarms;
  const timing = data.timing;
  const extractSeconds = timing.avg_extract_ms ? `${(timing.avg_extract_ms / 1000).toFixed(1)} s` : "n/a";

  return (
    <Section kicker="Evaluation" title="Catch rate, false alarms and speed">
      <p className="lede">
        {data.analyses} analyses were written by an LLM from real filing figures. Known errors were injected into copies
        by a script ({data.injected} copies), and each copy went through ProofLine and through a naive LLM checker.
      </p>
      <div className="stats">
        <Stat value={percent(data.overall.catch_rate, 1)} label="Injected errors caught by ProofLine"
          detail={`Naive LLM checker: ${percent(data.overall.naive_catch_rate, 1)}`} tone="good" />
        <Stat value={percent(fa.rate, 2)} label="False alarms on untouched numbers"
          detail={`${fa.red_flags} of ${fa.untouched_numbers.toLocaleString()} figures. Naive checker flagged ${fa.naive_flagged_clean} of ${fa.naive_clean_total} clean reports.`} />
        <Stat value={`${timing.avg_check_ms.toFixed(1)} ms`} label="Checking time per report"
          detail={`Plain code. The one LLM read takes ${extractSeconds} on average.`} />
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Injected error</th>
              <th className="num-col">Copies</th>
              <th>ProofLine caught</th>
              <th>Right diagnosis</th>
              <th>Naive LLM caught</th>
            </tr>
          </thead>
          <tbody>
            {data.by_error_type.map((row) => (
              <tr key={row.id}>
                <td>
                  <strong>{row.label}</strong>
                  <div className="muted">Expected verdict: {row.expected.replace("_", " ").toLowerCase()}</div>
                </td>
                <td className="num-col">{row.n}</td>
                <td><RateCell rate={row.catch_rate} count={row.caught} total={row.n} goodWhenZero={false} /></td>
                <td><RateCell rate={row.exact_rate} count={row.exact_diagnosis} total={row.n} goodWhenZero={false} /></td>
                <td><RateCell rate={row.naive_catch_rate} count={row.naive_caught} total={row.n} goodWhenZero={false} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="stats stats-small">
        <Stat value={`${fa.clean_reports_certified}/${data.analyses}`} label="Untouched analyses fully certified" />
        <Stat value={percent(fa.could_not_check_rate, 1)} label="Figures ProofLine could not check"
          detail="Amber: no source data, or not tied to a claim" />
        <Stat value={timing.avg_numbers_per_report} label="Figures per analysis" />
        <Stat value={data.overall.missed} label="Injected errors that passed as verified" tone={data.overall.missed === 0 ? "good" : "bad"} />
      </div>
      {data.misses.length > 0 && (
        <Notice tone="warn" title="Injected errors not flagged red">
          <ul className="plain">
            {data.misses.map((m, i) => (
              <li key={i}><code>{m.error}</code> {m.original} changed to {m.replacement}: {m.outcome.toLowerCase()}</li>
            ))}
          </ul>
        </Notice>
      )}
      {fa.examples.length > 0 && (
        <Notice tone="muted" title="Red flags on untouched text">
          <ul className="plain">
            {fa.examples.map((x, i) => <li key={i}><strong>{x.text}</strong> {x.reason}</li>)}
          </ul>
        </Notice>
      )}
      <p className="fineprint">Model: {data.model}. Generated {data.generated_at}. Dataset and labels are in the eval folder.</p>
    </Section>
  );
}
