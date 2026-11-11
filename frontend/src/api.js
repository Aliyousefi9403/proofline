// Thin wrapper over the ProofLine API. Every call returns data or throws an Error with a readable message.

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`/api${path}`, options);
  } catch {
    throw new Error("Cannot reach the ProofLine server. Is it running on port 8000?");
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload && payload.detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => d.msg).join("; ")
      : detail || `Request failed (${response.status}).`;
    throw new Error(message);
  }
  return payload;
}

const post = (path, body) =>
  request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

export const api = {
  meta: () => request("/meta"),
  samples: () => request("/samples"),
  verify: (text, defences, extractor = "auto") => post("/verify", { text, defences, extractor }),
  naive: (text) => post("/naive", { text }),
  ask: (question, withholdUnverified) => post("/ask", { question, withhold_unverified: withholdUnverified }),
  guard: (text, withholdUnverified) => post("/guard", { text, withhold_unverified: withholdUnverified }),
  replay: (certificate, text) => post("/replay", { certificate, text }),
  audit: (limit = 80) => request(`/audit?limit=${limit}`),
  auditVerify: (simulateTamper = false) => post("/audit/verify", { simulate_tamper: simulateTamper }),
  results: (name) => request(`/results/${name}`),
  facts: (ticker) => request(`/facts/${ticker}`),
};
