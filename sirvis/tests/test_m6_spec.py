"""Reading a benchmark specification (§11.5).

The behaviour worth testing here is the refusal. A parser that accepts what it
cannot honour produces a run that looks complete and measured something else —
and §11.5's freezing rule means the resulting evidence is then compared against
results the specification really did describe.
"""

from __future__ import annotations

import pytest

from sirvis.benchmarks import load_experiment, parse_experiment
from sirvis.errors import InvalidConfigurationError

MINIMAL = """
suite: perf
target:
  model: some-model
tests:
  - id: t1
    prompt: hello
"""


def test_the_defaults_are_the_ones_the_specification_states() -> None:
    """§11.7 sets warmups and repetitions, not this code."""
    spec = parse_experiment(MINIMAL)

    assert (spec.warmups, spec.repetitions) == (2, 5)
    assert spec.environment_mode == "shared"
    assert spec.tests[0].generation.temperature == 0.0


def test_a_setting_this_engine_cannot_honour_is_refused_with_the_milestone() -> None:
    """The expensive silent failure: an `evaluator` dropped on the floor makes a
    performance number look like a graded one."""
    with pytest.raises(InvalidConfigurationError) as failure:
        parse_experiment(MINIMAL + "    evaluator:\n      type: json_schema\n")

    assert "evaluator" in str(failure.value)
    assert "M18" in str(failure.value)


def test_versions_are_text_so_evidence_identity_does_not_shift() -> None:
    """§12.2 hashes the suite version. `1` and `"1"` must not be two suites."""
    assert parse_experiment(MINIMAL + "version: 1\n").suite_version == "1"
    assert parse_experiment(MINIMAL + "suite_version: 1.0\n").suite_version == "1.0"


def test_an_experiment_with_no_model_is_refused_rather_than_guessed_at() -> None:
    with pytest.raises(InvalidConfigurationError, match="target.model"):
        parse_experiment("suite: perf\ntests:\n  - id: t\n    prompt: hi\n")


def test_an_experiment_with_no_tests_is_refused() -> None:
    with pytest.raises(InvalidConfigurationError, match="at least one test"):
        parse_experiment("suite: perf\ntarget:\n  model: m\n")


def test_zero_repetitions_measures_nothing_and_is_refused() -> None:
    """One is permitted — the spread then publishes as absent rather than zero —
    but zero is an experiment with no observations in it."""
    with pytest.raises(InvalidConfigurationError, match="at least one"):
        parse_experiment(MINIMAL + "repetitions: 0\n")


def test_an_unknown_environment_mode_is_refused() -> None:
    """§11.1: `controlled` and `shared` are never silently compared, which is
    only possible while every experiment declares one of exactly those two."""
    with pytest.raises(InvalidConfigurationError, match="environment"):
        parse_experiment(MINIMAL + "environment: probably-fine\n")


def test_the_shipped_example_is_the_one_the_milestone_names() -> None:
    """M6's exit criterion names `examples/basic.yaml` by path. A test that only
    parsed a string would let that file rot."""
    spec = load_experiment("examples/basic.yaml")

    assert spec.model_key
    assert spec.tests and spec.tests[0].generation.max_tokens == 256


def test_a_missing_file_says_which_path_it_tried() -> None:
    with pytest.raises(InvalidConfigurationError, match="nowhere.yaml"):
        load_experiment("nowhere.yaml")
