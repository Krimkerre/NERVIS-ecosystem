"""The benchmark engine — the part of SIRVIS that produces evidence (§11).

Everything else in this service describes or serves measurements. This is the
only place that takes them, which is why it is also the only place that loads a
model: §9 requires every load and unload to flow through the Resource Manager,
and the engine holds a lease rather than driving the runtime itself.
"""

from sirvis.benchmarks.engine import ExperimentOutcome, run_experiment
from sirvis.benchmarks.spec import (
    BenchmarkTest,
    ExperimentSpec,
    GenerationConfig,
    load_experiment,
    parse_experiment,
)

__all__ = [
    "BenchmarkTest",
    "ExperimentOutcome",
    "ExperimentSpec",
    "GenerationConfig",
    "load_experiment",
    "parse_experiment",
    "run_experiment",
]
