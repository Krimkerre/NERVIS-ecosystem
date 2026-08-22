"""The Clarvis wire-contract suite (RAVIS.md §8).

Its exit criterion is stated as a sentence rather than a metric, and the sentence
is the useful part: **Clarvis cannot tell that an intermediary was inserted.**
"""

from ravis.compatibility.clarvis.conformance import ConformanceResult, run_suite

__all__ = ["ConformanceResult", "run_suite"]
