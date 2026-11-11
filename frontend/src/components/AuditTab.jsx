import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { STATUS } from "../status.js";
import { Badge, Button, Empty, Icon, Notice, Section } from "./ui.jsx";

function describe(entry) {
  if (entry.type === "certificate") return `Certificate ${entry.certificate_id.slice(0, 12)} issued: ${entry.status}`;
  if (entry.type === "unchecked") return `${entry.token.text}: number not covered by any claim`;
  if (entry.type === "verdict") {
    const c = entry.claim;
    return `${c.company} ${c.metric.replaceAll("_", " ")} FY${c.fiscal_year ?? "?"}: ${c.span}`;
  }
  return entry.type || "entry";
}

export default function AuditTab() {
  const [log, setLog] = useState(null);
  const [check, setCheck] = useState(null);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  const refresh = useCallback(() => {
    api.audit(120).then(setLog).catch((e) => setError(e.message));
  }, []);
  useEffect(refresh, [refresh]);

  async function run(simulate) {
    setBusy(simulate ? "tamper" : "verify");
    setError(null);
    try {
      setCheck(await api.auditVerify(simulate));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  const entries = log ? [...log.entries].reverse() : [];
  return (
    <Section
      kicker="Audit log"
      title="Every verdict, hash-chained"
      aside={
        <div className="actions">
          <Button variant="primary" icon="chain" busy={busy === "verify"} onClick={() => run(false)}>Verify chain</Button>
          <Button busy={busy === "tamper"} onClick={() => run(true)}>Simulate tampering</Button>
          <Button icon="replay" onClick={refresh}>Refresh</Button>
        </div>
      }
    >
      <p className="lede">
        Append-only. Each entry stores the hash of the one before it, so editing or deleting any line breaks every hash
        after it. A regulator can recompute the chain without trusting this app.
      </p>
      {error && <Notice tone="bad">{error}</Notice>}
      {check && (
        <Notice tone={check.ok ? "good" : "bad"} title={check.ok ? "Chain intact" : check.simulated ? "Tampering detected (simulation)" : "Chain broken"}>
          {check.ok
            ? `${check.entries} entries recomputed. Every hash matches. Head: ${check.head.slice(0, 20)}`
            : `Entry #${check.broken_at}: ${check.reason}.${check.simulated ? " One verdict was flipped in a copy held in memory. The log file was not changed." : ""}`}
        </Notice>
      )}
      {!log && <Empty title="Loading">Reading the audit log.</Empty>}
      {log && entries.length === 0 && <Empty title="The log is empty">Verify a report and its verdicts appear here.</Empty>}
      {log && entries.length > 0 && (
        <div className="table-wrap">
          <table className="table table-compact">
            <thead>
              <tr><th className="num-col">#</th><th>Time (UTC)</th><th>Record</th><th>Verdict</th><th>Source</th><th>Hash</th><th>Previous</th></tr>
            </thead>
            <tbody>
              {entries.map((entry) => {
                const meta = STATUS[entry.status];
                const broken = check && !check.ok && check.broken_at === entry.index;
                return (
                  <tr key={entry.index} className={`${entry.type === "certificate" ? "row-certificate" : ""} ${broken ? "row-broken" : ""}`}>
                    <td className="num-col">{entry.index}</td>
                    <td className="mono">{(entry.timestamp || "").slice(11, 19)}</td>
                    <td>{entry.type === "certificate" && <Icon name="shield" size={12} />} {describe(entry)}</td>
                    <td>{meta ? <Badge tone={meta.tone} icon={meta.icon}>{meta.label}</Badge> : entry.status || ""}</td>
                    <td className="mono">{(entry.accessions || []).join(", ")}</td>
                    <td className="mono">{(entry.hash || "").slice(0, 10)}</td>
                    <td className="mono muted">{(entry.prev_hash || "").slice(0, 10)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {log && <p className="fineprint">Showing the latest {entries.length} of {log.total} entries.</p>}
    </Section>
  );
}
