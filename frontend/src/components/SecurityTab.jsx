import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { percent } from "../status.js";
import { Button, Empty, Notice, RateCell, Section, Stat } from "./ui.jsx";

export default function SecurityTab({ onOpen }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { api.results("redteam").then(setData).catch((e) => setError(e.message)); }, []);

  if (error) return <Notice tone="bad">{error}</Notice>;
  if (!data) return <Empty title="Loading">Reading red-team results.</Empty>;
  if (!data.available) {
    return <Empty title="No red-team results yet">Run <code>uv run python redteam/run_attacks.py</code> to generate them.</Empty>;
  }
  const systems = data.systems;
  const rows = [...data.attacks, { id: "none", label: data.control.label, description: data.control.description, results: data.control.results, control: true }];

  return (
    <Section kicker="Security" title="Prompt injection: five attacks, three systems">
      <p className="lede">
        {data.reports} reports, each with one known wrong number, were poisoned with each attack: {data.poisoned} poisoned
        reports in all. An attack succeeds when the wrong number gets through with no flag. Lower is better.
      </p>
      <div className="stats">
        {systems.map((s) => (
          <Stat key={s.id} value={percent(data.overall[s.id].rate)}
            tone={data.overall[s.id].rate === 0 ? "good" : s.id === "on" ? "bad" : undefined}
            label={s.label} detail={`${data.overall[s.id].success} of ${data.overall[s.id].total} attacks succeeded`} />
        ))}
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Attack</th>
              {systems.map((s) => <th key={s.id} title={s.detail}>{s.label}</th>)}
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} className={row.control ? "row-control" : ""}>
                <td>
                  <strong>{row.label}</strong>
                  <div className="muted">{row.description}</div>
                </td>
                {systems.map((s) => (
                  <td key={s.id}>
                    <RateCell rate={row.results[s.id].rate} count={row.results[s.id].success} total={row.results[s.id].total} />
                  </td>
                ))}
                <td className="cell-action">
                  {row.example && <Button onClick={() => onOpen(row.example.text)}>Open example</Button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.breaches.length === 0 ? (
        <Notice tone="good" title="No attack beat the full system in this run">
          That is a measurement on {data.poisoned} reports, not a guarantee. The design reason it holds: the model
          cannot issue verdicts, every number is re-read from the text by code, and the coverage check flags any number
          left out. A figure disguised in a form the number parser does not recognise would get through; that is the
          known limit.
        </Notice>
      ) : (
        <Notice tone="bad" title={`${data.breaches.length} attack${data.breaches.length > 1 ? "s" : ""} beat the full system`}>
          <ul className="plain">
            {data.breaches.map((b, i) => (
              <li key={i}>
                <strong>{b.attack}</strong> on {b.report_id}: <q>{b.wrong_text}</q> was not flagged.{" "}
                <button className="linkish" onClick={() => onOpen(b.text)}>Open it</button>
              </li>
            ))}
          </ul>
        </Notice>
      )}
      <p className="fineprint">
        Models: {data.model} for extraction and for the naive checker. Generated {data.generated_at}. How the full
        system flagged the wrong figure: {Object.entries(data.flagged_by).filter(([, n]) => n > 0).map(([k, n]) => `${k.replace("_", " ").toLowerCase()} ${n}`).join(", ")}.
      </p>
    </Section>
  );
}
