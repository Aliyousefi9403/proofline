import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { STATUS, percent } from "../status.js";
import { Badge, Button, Empty, Notice, RateCell, Section, Stat } from "./ui.jsx";

export default function LeaderboardTab({ onOpen }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { api.results("leaderboard").then(setData).catch((e) => setError(e.message)); }, []);

  if (error) return <Notice tone="bad">{error}</Notice>;
  if (!data) return <Empty title="Loading">Reading leaderboard results.</Empty>;
  if (!data.available) {
    return <Empty title="No leaderboard yet">Run <code>uv run python eval/leaderboard.py</code> to generate it.</Empty>;
  }
  const memory = data.overall.memory;
  const source = data.overall.source;
  const categories = Object.entries(data.categories);

  return (
    <Section kicker="Leaderboard" title="How often do AI models get financial figures wrong?">
      <p className="lede">
        {data.questions} questions about five companies went to {data.models.length} models, once from memory and once
        with the 10-K financial statements supplied. Every answer was scored by ProofLine against the filings. No human
        labelling, and no staged examples.
      </p>
      <div className="stats">
        <Stat value={percent(memory.wrong_answer_rate)} label="Answers from memory with a wrong number"
          detail={`${memory.answers_with_wrong_number} of ${memory.answers} answers`} />
        <Stat value={percent(source.wrong_answer_rate)} label="Answers with the 10-K supplied with a wrong number"
          detail={`${source.answers_with_wrong_number} of ${source.answers} answers`} />
        <Stat value={(memory.numbers + source.numbers).toLocaleString()} label="Figures checked"
          detail={`${memory.wrong_numbers + source.wrong_numbers} contradicted a filing`} />
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Model</th>
              <th>Mode</th>
              <th>Answers with a wrong number</th>
              <th className="num-col">Wrong value</th>
              <th className="num-col">Wrong period</th>
              <th className="num-col">Scale error</th>
              <th className="num-col">Could not check</th>
              <th className="num-col">Fully certified</th>
            </tr>
          </thead>
          <tbody>
            {data.table.map((row) => (
              <tr key={`${row.model}-${row.mode}`}>
                <td><strong>{row.model_label}</strong></td>
                <td>{row.mode_label}</td>
                <td>
                  <RateCell rate={row.wrong_answer_rate} count={row.answers_with_wrong_number} total={row.answers} />
                </td>
                <td className="num-col">{row.by_verdict.WRONG_VALUE}</td>
                <td className="num-col">{row.by_verdict.WRONG_PERIOD}</td>
                <td className="num-col">{row.by_verdict.SCALE_ERROR}</td>
                <td className="num-col">{row.unverifiable_numbers}</td>
                <td className="num-col">{row.fully_certified_answers}/{row.answers}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 className="subhead">Where the errors are: answers with a wrong number, by question type</h3>
      <div className="table-wrap">
        <table className="table table-compact">
          <thead>
            <tr>
              <th>Model</th>
              <th>Mode</th>
              {categories.map(([key, label]) => <th key={key} className="num-col">{label}</th>)}
            </tr>
          </thead>
          <tbody>
            {data.table.map((row) => (
              <tr key={`${row.model}-${row.mode}-cat`}>
                <td>{row.model_label}</td>
                <td>{row.mode_label}</td>
                {categories.map(([key]) => (
                  <td key={key} className="num-col">
                    {row.by_category[key].with_wrong}/{row.by_category[key].answers}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 className="subhead">Real failures</h3>
      <p className="muted">The answers with the most contradicted figures. Open one to see every number checked.</p>
      <div className="failures">
        {data.failures.map((f) => (
          <article key={f.id} className="card">
            <div className="card-head">
              <div>
                <div className="kicker">{f.model_label}, {f.mode === "memory" ? "from memory" : "10-K supplied"}</div>
                <h3>{f.question}</h3>
              </div>
              <Button onClick={() => onOpen(f.answer)}>Open in checker</Button>
            </div>
            <p className="quote">{f.answer}</p>
            <ul className="wrong-list">
              {f.wrong.map((w, i) => (
                <li key={i}>
                  <Badge tone="bad" icon="cross">{STATUS[w.status].label}</Badge>
                  <span><strong>{w.text}</strong> {w.reason}</span>
                </li>
              ))}
            </ul>
          </article>
        ))}
        {data.failures.length === 0 && (
          <Notice tone="good" title="No contradicted figures in this run">Every figure checked matched the filings.</Notice>
        )}
      </div>
      <p className="fineprint">
        Scored with {data.scorer_model} as the extractor. Generated {data.generated_at}. A model that scores clean in
        one mode is shown as clean: the checker is the same for every row.
      </p>
    </Section>
  );
}
