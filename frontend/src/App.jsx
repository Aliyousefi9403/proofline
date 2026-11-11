import React, { useEffect, useState } from "react";
import { api } from "./api.js";
import AuditTab from "./components/AuditTab.jsx";
import DataTab from "./components/DataTab.jsx";
import EvalTab from "./components/EvalTab.jsx";
import GuardTab from "./components/GuardTab.jsx";
import LeaderboardTab from "./components/LeaderboardTab.jsx";
import SecurityTab from "./components/SecurityTab.jsx";
import VerifyTab from "./components/VerifyTab.jsx";
import { Icon } from "./components/ui.jsx";

const TABS = [
  { id: "verify", label: "Checker" },
  { id: "guard", label: "Guardrail" },
  { id: "security", label: "Security" },
  { id: "leaderboard", label: "Leaderboard" },
  { id: "eval", label: "Evaluation" },
  { id: "audit", label: "Audit log" },
  { id: "data", label: "Source data" },
];

const LLM_LABEL = {
  anthropic: "Anthropic API",
  "claude-cli": "Claude CLI",
  none: "Offline: rule-based extractor",
};

export default function App() {
  const [tab, setTab] = useState(() => {
    const fromHash = window.location.hash.slice(1);
    return TABS.some((t) => t.id === fromHash) ? fromHash : "verify";
  });
  const [meta, setMeta] = useState(null);
  const [draft, setDraft] = useState(null);

  useEffect(() => { api.meta().then(setMeta).catch(() => setMeta(null)); }, []);
  useEffect(() => { window.location.hash = tab; }, [tab]);

  function openInChecker(text) {
    setDraft({ text, at: Date.now() });
    setTab("verify");
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true"><Icon name="check" size={16} /></span>
          <span className="brand-name">ProofLine</span>
          <span className="brand-tag">Assurance for AI-written financial analysis</span>
        </div>
        <nav className="tabs" aria-label="Sections">
          {TABS.map((t) => (
            <button key={t.id} className={tab === t.id ? "is-on" : ""} aria-current={tab === t.id ? "page" : undefined}
              onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </nav>
        <div className="env" title="Where the single LLM call goes">
          <span className={`dot ${meta && meta.llm.provider !== "none" ? "dot-on" : ""}`} />
          {meta ? LLM_LABEL[meta.llm.provider] || meta.llm.provider : "Connecting"}
        </div>
      </header>
      <main className="main">
        <div hidden={tab !== "verify"}>
          <VerifyTab meta={meta} draft={draft} onDraftUsed={() => setDraft(null)} />
        </div>
        {tab === "guard" && <GuardTab />}
        {tab === "security" && <SecurityTab onOpen={openInChecker} />}
        {tab === "leaderboard" && <LeaderboardTab onOpen={openInChecker} />}
        {tab === "eval" && <EvalTab />}
        {tab === "audit" && <AuditTab />}
        {tab === "data" && <DataTab meta={meta} />}
      </main>
      <footer className="footer">
        <span>US filings (10-K), numeric claims. {meta ? `${meta.companies.length} companies cached.` : ""}</span>
        <span>{meta ? `${meta.engine}, data snapshot ${meta.data_snapshot.slice(0, 10)}` : ""}</span>
      </footer>
    </div>
  );
}
