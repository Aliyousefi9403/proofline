import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { REPORT_STATUS } from "../status.js";
import { Badge, Button, Icon } from "./ui.jsx";

function Check({ ok, children }) {
  return (
    <li className={ok ? "text-good" : "text-bad"}>
      <Icon name={ok ? "check" : "cross"} size={13} />
      <span>{children}</span>
    </li>
  );
}

export default function CertificateCard({ certificate, text }) {
  const [replay, setReplay] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  // Deep link for demos: &replay=1 presses Replay as soon as a certificate arrives.
  useEffect(() => {
    if (certificate && new URLSearchParams(window.location.search).get("replay")) runReplay();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [certificate?.certificate_id]);
  if (!certificate) return null;
  const body = certificate.body;
  const meta = REPORT_STATUS[body.status] || REPORT_STATUS.EMPTY;

  async function runReplay() {
    setBusy(true);
    setError(null);
    try {
      setReplay(await api.replay(certificate, text));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  function download() {
    const blob = new Blob([JSON.stringify(certificate, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `proofline-certificate-${certificate.certificate_id.slice(0, 12)}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className={`certificate tone-${meta.tone}`}>
      <div className="certificate-head">
        <div className="seal" aria-hidden="true"><Icon name="shield" size={22} /></div>
        <div>
          <div className="kicker">Certificate</div>
          <div className="certificate-title">
            <Badge tone={meta.tone} icon={meta.icon}>{meta.label}</Badge>
            <code>{certificate.certificate_id.slice(0, 16)}</code>
          </div>
        </div>
        <div className="certificate-actions">
          <Button icon="replay" busy={busy} onClick={runReplay}>Replay</Button>
          <Button icon="download" onClick={download}>JSON</Button>
        </div>
      </div>
      <dl className="certificate-grid">
        <div><dt>Checks recorded</dt><dd>{body.checks.length + body.unchecked.length}</dd></div>
        <div><dt>Signature</dt><dd>{certificate.signature.algorithm}</dd></div>
        <div><dt>Audit log entry</dt><dd>#{certificate.anchor.index}</dd></div>
        <div><dt>Data snapshot</dt><dd><code>{body.data_snapshot.slice(0, 10)}</code></dd></div>
      </dl>
      {error && <p className="text-bad">{error}</p>}
      {replay && (
        <div className="replay">
          <strong>
            {replay.valid ? "Replay matches." : "Replay does not match."} {replay.verdicts_replayed} verdicts recomputed in{" "}
            {replay.duration_ms} ms with {replay.llm_calls} LLM calls.
          </strong>
          <ul>
            <Check ok={replay.signature_valid}>Signature is valid</Check>
            <Check ok={replay.anchored_in_log}>Anchored in an intact audit chain</Check>
            <Check ok={replay.data_snapshot_match}>Same filing data snapshot</Check>
            <Check ok={replay.verdicts_match}>Every verdict recomputed to the same result</Check>
            {replay.text_match !== null && <Check ok={replay.text_match}>Report text matches the certified hash</Check>}
          </ul>
        </div>
      )}
    </div>
  );
}
