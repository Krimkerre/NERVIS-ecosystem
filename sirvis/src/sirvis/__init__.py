"""SIRVIS — the evidence plane. It measures local models and says what it found.

SIRVIS.md is the specification. The one sentence that shapes every module here
is §12.1's provenance invariant: **a value's provenance is never upgraded.**
Something `ESTIMATED` does not become `MEASURED` because it was copied, averaged
or stored; something `UNKNOWN` does not become a zero because a column needed
filling. RAVIS routes on what SIRVIS says, so a number that overstates what was
actually observed becomes a wrong route on somebody else's machine.
"""
