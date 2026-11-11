import React, { useMemo } from "react";
import { STATUS } from "../status.js";
import { Icon } from "./ui.jsx";

// Split the report into plain runs, checked numbers and instruction-like sentences.
function buildSegments(text, marks, injections) {
  const points = new Set([0, text.length]);
  marks.forEach((m) => { points.add(m.start); points.add(m.end); });
  injections.forEach((i) => { points.add(i.start); points.add(i.end); });
  const sorted = [...points].filter((p) => p >= 0 && p <= text.length).sort((a, b) => a - b);
  const segments = [];
  for (let i = 0; i < sorted.length - 1; i += 1) {
    const [start, end] = [sorted[i], sorted[i + 1]];
    if (start === end) continue;
    segments.push({
      start,
      text: text.slice(start, end),
      mark: marks.find((m) => m.start <= start && m.end >= end) || null,
      injected: injections.some((x) => x.start <= start && x.end >= end),
    });
  }
  return segments;
}

export default function ReportView({ result, selected, onSelect }) {
  const text = result.report.text;
  const marks = useMemo(() => {
    const fromVerdicts = result.verdicts
      .map((v, index) => (v.token ? { key: `v${index}`, kind: "verdict", index, status: v.status, ...v.token } : null))
      .filter(Boolean);
    const fromUnchecked = result.unchecked.map((u, index) => ({
      key: `u${index}`, kind: "unchecked", index, status: "UNCHECKED", ...u.token,
    }));
    return [...fromVerdicts, ...fromUnchecked];
  }, [result]);
  const injections = useMemo(() => result.injections.filter((i) => i.start !== null && i.start !== undefined), [result]);
  const segments = useMemo(() => buildSegments(text, marks, injections), [text, marks, injections]);

  return (
    <div className="report" aria-label="Checked report">
      {segments.map((segment) => {
        if (!segment.mark) {
          return segment.injected ? (
            <span key={segment.start} className="injected" title="Reads like an instruction to an AI. ProofLine treats it as text.">
              {segment.text}
            </span>
          ) : (
            <React.Fragment key={segment.start}>{segment.text}</React.Fragment>
          );
        }
        const meta = STATUS[segment.mark.status];
        const active = selected === segment.mark.key;
        return (
          <button
            key={segment.start}
            type="button"
            className={`num tone-${meta.tone} ${active ? "is-active" : ""} ${segment.injected ? "in-injection" : ""}`}
            onClick={() => onSelect(segment.mark.key)}
            aria-pressed={active}
            title={`${meta.label}: ${meta.blurb}`}
          >
            <Icon name={meta.icon} size={11} />
            {segment.text}
          </button>
        );
      })}
    </div>
  );
}
