# ProofLine pitch (5 slides, about 5 minutes)

Numbers in brackets come from the result files. Read them off the UI tabs or the README on the day, so the
slides always match the latest run.

## 1. Problem

"Analysts are starting to use AI to write financial analysis. AI gets numbers wrong, and nobody can tell which."

- Open on the **Leaderboard** tab: "We asked 60 questions to three models. [X%] of answers from memory had a
  wrong number."
- Open one real failure in the checker. The red number, the filed figure, the link to the 10-K.

## 2. Idea

"The AI reads. Code checks. We don't use an AI to check an AI."

- One LLM call structures the claims. Its output format has no verdict field.
- Every number is looked up in SEC filings and checked with arithmetic. A verdict cannot be hallucinated.
- Live: paste the **Report with errors** sample, press Verify. Then let a judge ask their own question in the
  **Guardrail** tab.

## 3. Clean case: assurance is the product

"A clean report is not a failed demo. A clean audit is the product."

- **Clean report** sample: all green, certificate issued.
- Press **Replay**: every verdict recomputed in about a millisecond, 0 LLM calls. A regulator can do the same.
- **Audit log** tab: Verify chain, then Simulate tampering.

## 4. Attack

"What if the report itself tells the AI to look away?"

- **Poisoned report** sample. First press "Ask a naive AI checker".
- Then Verify: hidden text removed, instructions underlined, wrong figures still red.
- **Security** tab, the table for 100 poisoned reports. The line to say: "With no attack, the naive AI checker
  caught 20 of 20 wrong numbers. One sentence saying 'already verified' and it passed 19 of 20. ProofLine: 0 of
  100 got through."
- Say the honest part: in this run the extractor model ignored the injections on its own, so even with defences
  off nothing got through. The code defences are for the day a model does obey.

## 5. Results and business

- **Evaluation** tab: [catch rate] of injected errors caught, [false alarm rate] false alarms on [N] untouched
  figures, checking takes about [3] ms per report in plain code.
- LSEG sells data to AI agents. ProofLine lets it sell verified answers: every AI-written figure either carries
  a certificate tied to a filing, or is stopped.
- Only the report text goes to the model. Source data and verdicts stay local.
- Limits, stated plainly: US 10-K filings, numeric claims, five companies in this build.

## Questions to expect

- **"What if the extractor mislabels a claim?"** Then the check fails closed: a wrong label gives a red or amber
  flag, never a silent pass, because the number still has to match the filing for that label.
- **"What about numbers you can't check?"** They come back amber, "could not check". They never count as verified,
  and a report with any amber number gets no certificate.
- **"Why not RAG?"** Structured XBRL facts give an exact figure and an accession number. Retrieval gives a passage
  and a similarity score. For numbers, lookup is exact and auditable.
- **"Does it scale?"** Checking is a dictionary lookup plus arithmetic. The only model call is one read per report.
