"""Calibration: the test run proving Codex's file rules before any real Codex task (design §10.4).

Owner decision D2 lets a Codex task start only once stricter file rules are proven to hold on the
exact Codex build RAVIS runs. For a pinned build, this is where that proof comes from. A run asks
sixteen questions (K1-K13, with K2b, K5a and K5c) inside RAVIS's one Codex process, on two
throwaway git projects the owner names, with the owner present: it uses the plan's allowance.

**How it starts.** Only while RAVIS runs with `RAVIS_CODEX_CALIBRATION=1` does the route
`POST /api/v1/codex/calibration/runs` exist (`api/management/codex_calibration.py`), and only the
owner's command-line credential may call it — `ravis codex calibrate`, or `tools/run.py codex
calibrate`, which runs it. No second Codex process ever starts on RAVIS's Codex home.

**The parts:**
- `plan.py` — the questions, the two projects, the decoys, the profile under test;
- `harness.py` — threads the run creates, each scenario's fixed approval answers, every answer
  audited, and a transcript of what passed between RAVIS and Codex;
- `scenarios.py`, `scenarios_processes.py` — the questions themselves, each a pass, a failure or a
  record, never a guess;
- `outputs.py` — redacted transcripts, the results summary, and the `tested_runtimes.json` entry,
  written **only** when every must-pass question passed in a full run;
- `runner.py` — one run: the projects' lock files held, the questions asked in order, the result;
- `command.py` — `ravis codex calibrate`, which starts a run and prints its progress plainly.
"""
