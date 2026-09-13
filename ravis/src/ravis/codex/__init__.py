"""Codex, the optional coding engine RAVIS hosts for Clarvis (runbook §2.2, RAVIS.md §15.1.2, M29).

Built in increments, in the order `STATUS.md`'s Codex build plan gives. **R1 is this much:** the
runtime check and version pin in `runtime.py` — where the executable is, whether OpenAI signed
it, and whether it is the build RAVIS pinned in `tested_runtimes.json` — run once at startup and
kept, so that `/v1/models` can decide whether to list `ravis/clarvis-codex` without running anything
(`api/openai/agent_backends.py`).

Not here yet: the Codex process itself, sign-in, the plan's allowance, accepting a new build and
the file-rules re-test (R2), the agent-session relay (R3) and the project lock (R4).
"""
