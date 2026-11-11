import React, { useState } from "react";
import { api } from "../api.js";
import { REPORT_STATUS } from "../status.js";
import CertificateCard from "./CertificateCard.jsx";
import ReportView from "./ReportView.jsx";
import { Badge, Button, Icon, Notice, Section, Toggle } from "./ui.jsx";

const QUESTIONS = [
  "What were NVIDIA's revenue, net income and gross margin in fiscal 2025, and how fast did revenue grow?",
  "By what percentage did Tesla's net income change from fiscal 2023 to fiscal 2025? Give both figures.",
  "Compare Apple and Microsoft on revenue and net income for fiscal 2024.",
  "What was Amazon's diluted EPS in fiscal 2024 and fiscal 2023, and the percentage change?",
];

const ACTION = {
  released: { tone: "good", icon: "check", label: "Released unchanged" },
  corrected: { tone: "bad", icon: "shield", label: "Corrected before release" },
  flagged: { tone: "warn", icon: "question", label: "Released with flags" },
};

function ReleasedText({ text, changes }) {
  // Highlight the replaced figures in the released text.
  const parts = [];
  let cursor = 0;
  changes.forEach((change, i) => {
    const at = text.indexOf(change.released, cursor);
    if (at === -1) return;
    parts.push(text.slice(cursor, at));
    parts.push(
      <mark key={i} className={`fix fix-${change.action}`} title={`${change.original} -> ${change.released}`}>
        {change.released}
      </mark>
    );
    cursor = at + change.released.length;
  });
  parts.push(text.slice(cursor));
  return <div className="report">{parts}</div>;
}

export default function GuardTab() {
  const [question, setQuestion] = useState(QUESTIONS[0]);
  const [withhold, setWithhold] = useState(false);
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function ask() {
    setBusy(true);
    setError(null);
    try {
      setData(await api.ask(question, withhold));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const action = data ? ACTION[data.action] : null;
  const status = data ? REPORT_STATUS[data.verification.summary.status] : null;

  return (
    <Section
      kicker="Guardrail mode"
      title="The same AI agent, with and without ProofLine in front of it"
      aside={<Toggle checked={withhold} onChange={setWithhold} label="Withhold unverifiable figures" hint="Otherwise they are released with a flag" />}
    >
      <p className="lede">
        Ask any question about the five companies. The agent answers from memory. ProofLine checks the answer before
        it reaches you: wrong figures are replaced with the filed figure, and the answer ships with a certificate.
      </p>
      <div className="ask">
        <textarea value={question} onChange={(e) => setQuestion(e.target.value)} rows={2} aria-label="Question for the agent" />
        <Button variant="primary" icon="arrow" busy={busy} disabled={question.trim().length < 3} onClick={ask}>Ask the agent</Button>
      </div>
      <div className="sample-row">
        <span className="muted">Try</span>
        {QUESTIONS.map((q) => (
          <button key={q} className="chip" onClick={() => setQuestion(q)}>{q.length > 58 ? `${q.slice(0, 56)}...` : q}</button>
        ))}
      </div>
      {error && <Notice tone="bad" title="Something went wrong">{error}</Notice>}
      {data && (
        <div className="duo">
          <div className="card">
            <div className="card-head">
              <div>
                <div className="kicker">Without ProofLine</div>
                <h3>What the agent said</h3>
              </div>
              <Badge tone={status.tone} icon={status.icon}>
                {data.verification.summary.wrong} wrong, {data.verification.summary.unverified} unverified
              </Badge>
            </div>
            <ReportView result={data.verification} selected={null} onSelect={() => {}} />
            <small className="muted">{data.agent.model}, answering from memory.</small>
          </div>
          <div className="card card-strong">
            <div className="card-head">
              <div>
                <div className="kicker">With ProofLine</div>
                <h3>What the reader receives</h3>
              </div>
              <Badge tone={action.tone} icon={action.icon}>{action.label}</Badge>
            </div>
            <ReleasedText text={data.released_text} changes={data.changes} />
            {data.changes.length > 0 && (
              <ul className="changes">
                {data.changes.map((c, i) => (
                  <li key={i}>
                    <span className="strike">{c.original}</span>
                    <Icon name="arrow" size={12} />
                    <strong>{c.released}</strong>
                    <span className="muted">{c.reason}</span>
                  </li>
                ))}
              </ul>
            )}
            <CertificateCard certificate={data.verification.certificate} text={data.agent.answer} />
          </div>
        </div>
      )}
    </Section>
  );
}
