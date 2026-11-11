import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { REPORT_STATUS, STATUS, STATUS_ORDER } from "../status.js";
import CertificateCard from "./CertificateCard.jsx";
import DetailPanel from "./DetailPanel.jsx";
import ReportView from "./ReportView.jsx";
import { Badge, Button, Icon, Notice, Toggle } from "./ui.jsx";

const DEFENCES_ON = { sanitize: true, bind: true, coverage: true, anti_evasion: true };
const DEFENCES_OFF = { sanitize: false, bind: false, coverage: false, anti_evasion: false };

function Counts({ counts }) {
  return (
    <div className="counts" role="list" aria-label="Verdict counts">
      {STATUS_ORDER.map((key) => {
        const meta = STATUS[key];
        const n = counts[key] || 0;
        return (
          <div key={key} className={`count tone-${meta.tone} ${n === 0 ? "is-zero" : ""}`} role="listitem" title={meta.blurb}>
            <Icon name={meta.icon} size={13} />
            <strong>{n}</strong>
            <span>{meta.label}</span>
          </div>
        );
      })}
    </div>
  );
}

function Timings({ result }) {
  const { extractor, timings } = result;
  const how = extractor.extractor === "rules"
    ? "rule-based extractor, no LLM"
    : `1 LLM call${extractor.cached ? ", cached" : ""} (${extractor.model})`;
  const extractMs = extractor.cached ? 0 : timings.extract_ms;
  return (
    <div className="timings">
      <span><Icon name="bolt" size={12} /> Reading: {extractMs >= 1000 ? `${(extractMs / 1000).toFixed(1)} s` : `${extractMs.toFixed(0)} ms`} ({how})</span>
      <span>Checking: {(timings.check_ms + timings.prepare_ms).toFixed(1)} ms, plain code, no model</span>
    </div>
  );
}

function NaiveCard({ review }) {
  return (
    <div className={`naive ${review.accurate ? "tone-good" : "tone-bad"}`}>
      <div className="naive-head">
        <div className="kicker">Naive baseline: an AI asked "is this accurate?"</div>
        <Badge tone={review.accurate ? "good" : "bad"} icon={review.accurate ? "check" : "cross"}>
          {review.accurate ? "Says: accurate" : "Says: not accurate"}
        </Badge>
      </div>
      <p>{review.summary}</p>
      {review.issues.length > 0 && (
        <ul>
          {review.issues.map((issue, i) => (
            <li key={i}><q>{issue.quote}</q> {issue.problem}</li>
          ))}
        </ul>
      )}
      <small>{review.model}. No source data, no audit trail, and nothing here can be replayed.</small>
    </div>
  );
}

export default function VerifyTab({ meta, draft, onDraftUsed }) {
  const [text, setText] = useState("");
  const [samples, setSamples] = useState([]);
  const [coverage, setCoverage] = useState(true);
  const [defended, setDefended] = useState(true);
  const [result, setResult] = useState(null);
  const [review, setReview] = useState(null);
  const [selected, setSelected] = useState(null);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [autoRun, setAutoRun] = useState(false);

  const metricLabels = useMemo(
    () => Object.fromEntries((meta?.metrics || []).map((m) => [m.key, m.label])),
    [meta]
  );

  // Deep link for demos: /?sample=poisoned&run=1 loads a sample and verifies it straight away.
  useEffect(() => {
    api.samples().then((data) => {
      setSamples(data.samples);
      const params = new URLSearchParams(window.location.search);
      const wanted = data.samples.find((s) => s.id === params.get("sample"));
      const initial = wanted || data.samples.find((s) => s.id === "clean");
      setText((current) => current || initial?.text || "");
      if (wanted && params.get("run")) setAutoRun(true);
    }).catch(() => setSamples([]));
  }, []);

  useEffect(() => {
    if (autoRun && text) {
      setAutoRun(false);
      verify();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRun, text]);

  useEffect(() => {
    if (draft) {
      load(draft.text);
      onDraftUsed();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  function load(next) {
    setText(next);
    setResult(null);
    setReview(null);
    setSelected(null);
    setError(null);
  }

  async function verify() {
    setBusy("verify");
    setError(null);
    setSelected(null);
    try {
      const defences = defended ? { ...DEFENCES_ON, coverage } : DEFENCES_OFF;
      const data = await api.verify(text, defences);
      setResult(data);
      const firstProblem = data.verdicts.findIndex((v) => v.status !== "VERIFIED" && v.token);
      if (firstProblem >= 0) setSelected(`v${firstProblem}`);
      else if (data.unchecked.length) setSelected("u0");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  async function askNaive() {
    setBusy("naive");
    setError(null);
    try {
      setReview(await api.naive(text));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  const item = useMemo(() => {
    if (!result || !selected) return null;
    const index = Number(selected.slice(1));
    if (selected[0] === "v") {
      const data = result.verdicts[index];
      return data ? { kind: "verdict", status: data.status, data } : null;
    }
    const data = result.unchecked[index];
    return data ? { kind: "unchecked", status: "UNCHECKED", data } : null;
  }, [result, selected]);

  const demo = samples.filter((s) => s.group === "demo");
  const attacks = samples.filter((s) => s.group === "attack");
  const status = result ? REPORT_STATUS[result.summary.status] : null;
  const unlocated = result ? result.verdicts.filter((v) => !v.token) : [];

  return (
    <div className="verify">
      <div className="pane pane-input">
        <div className="pane-title">
          <h2>Report</h2>
          <span className="muted">{text.length.toLocaleString()} characters</span>
        </div>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Paste AI-written analysis of Apple, Microsoft, NVIDIA, Tesla or Amazon."
          spellCheck="false"
          aria-label="Report text"
        />
        <div className="sample-row">
          <span className="muted">Samples</span>
          {demo.map((s) => (
            <button key={s.id} className="chip" onClick={() => load(s.text)} title={s.description}>{s.title}</button>
          ))}
        </div>
        {attacks.length > 0 && (
          <div className="sample-row">
            <span className="muted">Attacks</span>
            {attacks.map((s) => (
              <button key={s.id} className="chip chip-attack" onClick={() => load(s.text)} title={s.description}>{s.title}</button>
            ))}
          </div>
        )}
        <div className="controls">
          <Toggle checked={coverage && defended} onChange={(v) => { setCoverage(v); if (v) setDefended(true); }}
            label="Coverage check" hint="Flag every number no claim accounts for" />
          <Toggle checked={defended} onChange={setDefended}
            label="All defences" hint="Hidden-text stripping, code re-parsing, anti-evasion" />
        </div>
        <div className="actions">
          <Button variant="primary" icon="shield" busy={busy === "verify"} disabled={!text.trim() || busy !== null} onClick={verify}>
            Verify against SEC filings
          </Button>
          <Button busy={busy === "naive"} disabled={!text.trim() || busy !== null} onClick={askNaive}>
            Ask a naive AI checker
          </Button>
        </div>
        {error && <Notice tone="bad" title="Something went wrong">{error}</Notice>}
        {review && <NaiveCard review={review} />}
        <p className="fineprint">
          Only the report text goes to the model. Filing data, verdicts and the audit log stay on this machine.
        </p>
      </div>

      <div className="pane pane-result">
        {!result && (
          <div className="placeholder">
            <div className="placeholder-mark"><Icon name="shield" size={30} /></div>
            <h2>We don't use an AI to check an AI.</h2>
            <p>
              A model reads the report and lists its numeric claims. Plain code checks each one against the company's
              SEC filings, so a verdict cannot be hallucinated and every verdict can be replayed.
            </p>
            <ol className="steps">
              <li><strong>Read</strong><span>One LLM call structures the claims. It cannot output a verdict.</span></li>
              <li><strong>Check</strong><span>Lookup plus arithmetic against 10-K data. No model.</span></li>
              <li><strong>Cover</strong><span>Every number in the text must be accounted for.</span></li>
              <li><strong>Certify</strong><span>A signed, hash-chained record anyone can replay.</span></li>
            </ol>
          </div>
        )}
        {result && (
          <>
            <div className={`banner tone-${status.tone}`}>
              <div className="banner-icon"><Icon name={status.icon} size={20} /></div>
              <div>
                <div className="banner-title">{status.label}</div>
                <div className="banner-text">{result.summary.headline}</div>
              </div>
            </div>
            <Counts counts={result.summary.counts} />
            {result.extractor.warning && <Notice tone="warn">{result.extractor.warning}</Notice>}
            {result.removals.length > 0 && (
              <Notice tone="warn" title="Hidden content removed before reading">
                <ul className="plain">
                  {result.removals.map((r, i) => (
                    <li key={i}><code>{r.kind.replace("_", " ")}</code> {r.content}</li>
                  ))}
                </ul>
              </Notice>
            )}
            {result.injections.length > 0 && (
              <Notice tone="warn" title={`${result.injections.length} instruction-like passage${result.injections.length > 1 ? "s" : ""} found`}>
                Text addressed to an AI is underlined below. It is treated as report text. The model cannot issue
                verdicts or change a number, so it has nothing to obey.
              </Notice>
            )}
            <div className="result-grid">
              <div>
                <ReportView result={result} selected={selected} onSelect={setSelected} />
                {unlocated.length > 0 && (
                  <Notice tone="warn" title="Claims not tied to a number in the text">
                    {unlocated.map((v, i) => <div key={i}>{v.claim.span}: {v.reason}</div>)}
                  </Notice>
                )}
                {result.dropped.length > 0 && (
                  <Notice tone="muted" title="Extractor output set aside">
                    {result.dropped.length} claim{result.dropped.length > 1 ? "s" : ""} could not be tied to the text
                    ({[...new Set(result.dropped.map((d) => d.reason.replaceAll("_", " ")))].join(", ")}). Their numbers
                    stay unchecked.
                  </Notice>
                )}
                <Timings result={result} />
                <CertificateCard certificate={result.certificate} text={text} />
              </div>
              <DetailPanel item={item} metricLabels={metricLabels} onClose={() => setSelected(null)} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
