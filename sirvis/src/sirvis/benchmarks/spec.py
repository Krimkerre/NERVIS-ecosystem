"""Reading a benchmark specification off disk (§11.5).

§11.5 puts benchmark definitions in version-controlled files, and the reason is
§11.5's own next sentence: once results exist for `clarvis-agent@1.0`, its
prompts and rules are **frozen**, and a change requires a new version. A prompt
that lives in code is a prompt that changes in a refactor, and every result
measured against the old one silently becomes incomparable.

Three decisions here are deliberate and would each be easy to get wrong.

**An unknown key is refused, not ignored.** §7.1's rule about load configuration
— never silently drop what was asked for — is the same rule one level up: a
specification with `evaluator:` today would run as though nothing had been asked
for, and the operator would read a performance number as though it had been
graded. Evaluators arrive at M18; until then saying so is the honest answer.

**The version is a string.** `version: 1` in YAML is an integer, `version: "1.0"`
is not, and evidence identity (§12.2) hashes it — so a suite that changed from
one to the other would silently produce new evidence IDs for the same suite.
Normalised to text once, here.

**Nothing is defaulted that changes what is measured.** Warmups and repetitions
have §11.7's defaults because those are stated in the specification; the model,
the prompt and the suite identity have none, because a benchmark that guesses
what it is measuring is worse than one that refuses to start.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

import yaml

from sirvis.errors import InvalidConfigurationError

# §11.7's defaults, stated there rather than chosen here.
DEFAULT_WARMUPS = 2
DEFAULT_REPETITIONS = 5

# §11.1's two environment modes. `shared` is the default because it is what an
# ordinary laptop actually offers, and claiming `controlled` without having
# arranged exclusivity would be the silent equivalence §11.1 forbids.
ENVIRONMENT_MODES = ("controlled", "shared")

# §11.7's orderings. `randomized` and `balanced` need more than one target to
# mean anything, so M6 accepts only the one it can honour.
ORDERINGS = ("fixed",)

# Ways to ask a reasoning model to stop reasoning, tried in this order.
#
# **Every one of them is a prompt change**, which is why they are named rather
# than applied invisibly: §11.5 freezes a suite's prompts once results exist, so
# a run that quietly appended one would produce a number that is not about the
# prompt the suite declares. A suppression that fires is recorded in the
# evidence identity, so an adapted measurement cannot be mistaken for the one
# that was asked for.
#
# There is no API-level switch to use instead. LM Studio accepts
# `chat_template_kwargs: {"enable_thinking": false}` and **ignores it** — probed
# on this machine, byte-identical to the baseline — which is §7.1's
# accepted-then-silently-dropped trap and exactly why this engine will not
# forward a setting it cannot verify.
THINKING_SUPPRESSIONS = ("no_think_suffix", "direct_system")

# `version` is accepted as a synonym for `suite_version`, because §11.5's own
# example writes a bare `version: 1` at the top of a definition file and a
# parser that refused the specification's own example would be wrong about which
# document is authoritative.
_EXPERIMENT_KEYS = {
    "id", "suite", "suite_version", "version", "role", "environment", "ordering",
    "seed", "target", "warmups", "repetitions", "tests", "notes", "tags",
    "suppress_thinking",
}
_TARGET_KEYS = {"model", "load"}
_TEST_KEYS = {"id", "version", "prompt", "system", "generation"}
_GENERATION_KEYS = {"temperature", "max_tokens", "top_p", "stop"}


@dataclass(frozen=True)
class GenerationConfig:
    """What to ask the model for, and therefore part of what was measured.

    Temperature defaults to zero: §11.4's performance workloads are
    "synthetic deterministic", and a benchmark run at the runtime's default
    temperature measures a different thing on every runtime.
    """

    temperature: float = 0.0
    max_tokens: int = 256
    top_p: float | None = None
    stop: tuple[str, ...] = ()

    def as_options(self) -> dict[str, Any]:
        """The request options, with absent settings genuinely absent.

        Not sent as nulls: a runtime reading `top_p: null` may or may not treat
        that as "no preference", and the difference would land inside a
        measurement.
        """
        options: dict[str, Any] = {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.top_p is not None:
            options["top_p"] = self.top_p
        if self.stop:
            options["stop"] = list(self.stop)
        return options


@dataclass(frozen=True)
class BenchmarkTest:
    """One prompt, frozen once results exist for it (§11.5)."""

    id: str
    version: str
    prompt: str
    generation: GenerationConfig = field(default_factory=GenerationConfig)
    system: str | None = None

    def messages(self) -> list[dict[str, Any]]:
        """The chat messages this test sends."""
        messages: list[dict[str, Any]] = []
        if self.system:
            messages.append({"role": "system", "content": self.system})
        messages.append({"role": "user", "content": self.prompt})
        return messages


@dataclass(frozen=True)
class ExperimentSpec:
    """§11.1's experiment, as far as a single-model run needs it.

    Runtime Sets, sweeps and configuration matrices are §11.1's other target
    types and belong to M9, M10 and M17. This carries a single model, and the
    field is named `model_key` rather than `model` to keep saying which kind of
    identifier it is: the name the *runtime* uses for a build, which §6 is
    careful to distinguish from the build's identity.
    """

    suite_id: str
    suite_version: str
    model_key: str
    tests: tuple[BenchmarkTest, ...]
    load: Mapping[str, Any] = field(default_factory=dict)
    warmups: int = DEFAULT_WARMUPS
    repetitions: int = DEFAULT_REPETITIONS
    environment_mode: str = "shared"
    ordering: str = "fixed"
    seed: int | None = None
    role: str = "general"
    notes: str = ""
    tags: tuple[str, ...] = ()
    # What to try when the model answers nothing at all, in order. Empty
    # disables it, and the run then reports the empty result as it always did.
    #
    # Default is on, and that is a deliberate reading of what a benchmark is
    # for: a build that spends its whole budget thinking is not *measured* by
    # recording that it said nothing, and "cannot be measured as asked" is a
    # more useful finding when it arrives with "but here is what it does when
    # asked differently". Nothing is hidden by it — the suppression that fired
    # lands in the evidence identity and in the record's validity notes.
    suppress_thinking: tuple[str, ...] = THINKING_SUPPRESSIONS

    def as_dict(self) -> dict[str, Any]:
        """The specification as stored beside its results (§11.9).

        Written whole into `experiment.json` so a result can be read years later
        without the file it came from — which is the point of preserving raw
        results at all.
        """
        return {
            "suite": {"id": self.suite_id, "version": self.suite_version},
            "suppress_thinking": list(self.suppress_thinking),
            "role": self.role,
            "target": {"model_key": self.model_key, "load": dict(self.load)},
            "warmups": self.warmups,
            "repetitions": self.repetitions,
            "environment_mode": self.environment_mode,
            "ordering": self.ordering,
            "seed": self.seed,
            "notes": self.notes,
            "tags": list(self.tags),
            "tests": [
                {
                    "id": test.id,
                    "version": test.version,
                    "prompt": test.prompt,
                    "system": test.system,
                    "generation": test.generation.as_options(),
                }
                for test in self.tests
            ],
        }


def load_experiment(path: str | Path) -> ExperimentSpec:
    """Read one specification file, or say precisely why it cannot be read."""
    location = Path(path).expanduser()
    try:
        text = location.read_text(encoding="utf-8")
    except OSError as failure:
        raise InvalidConfigurationError(
            f"cannot read the benchmark specification at {location}: {failure}",
            path=str(location),
        ) from failure
    return parse_experiment(text, source=str(location))


def parse_experiment(text: str, source: str = "<string>") -> ExperimentSpec:
    """Parse and validate a specification, refusing anything it cannot honour."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as failure:
        raise InvalidConfigurationError(
            f"{source} is not valid YAML: {failure}", path=source
        ) from failure
    if not isinstance(document, dict):
        raise InvalidConfigurationError(
            f"{source} must be a mapping, not {type(document).__name__}", path=source
        )

    _refuse_unknown(document, _EXPERIMENT_KEYS, source, "experiment")
    target = _mapping(document.get("target"), source, "target")
    _refuse_unknown(target, _TARGET_KEYS, source, "target")

    model_key = target.get("model")
    if not isinstance(model_key, str) or not model_key.strip():
        raise InvalidConfigurationError(
            f"{source} must name the model to measure as `target.model`", path=source
        )

    tests = _parse_tests(document.get("tests"), source)
    warmups = _whole_number(document.get("warmups", DEFAULT_WARMUPS), source, "warmups")
    repetitions = _whole_number(
        document.get("repetitions", DEFAULT_REPETITIONS), source, "repetitions"
    )
    if repetitions < 1:
        # §11.7's whole argument is that a single take can measure noise. One is
        # permitted — the spread then publishes as absent rather than as zero —
        # but zero repetitions is an experiment that measures nothing.
        raise InvalidConfigurationError(
            f"{source} asks for {repetitions} measured repetitions; at least one is needed",
            path=source,
        )

    environment = _one_of(document.get("environment", "shared"), ENVIRONMENT_MODES,
                          source, "environment")
    ordering = _one_of(document.get("ordering", "fixed"), ORDERINGS, source, "ordering")

    return ExperimentSpec(
        suite_id=_text(document.get("suite") or document.get("id"), source, "suite"),
        suite_version=_version(document.get("suite_version", document.get("version", 1))),
        model_key=model_key.strip(),
        tests=tests,
        load=_mapping(target.get("load", {}), source, "target.load"),
        warmups=warmups,
        repetitions=repetitions,
        environment_mode=environment,
        ordering=ordering,
        seed=document.get("seed"),
        role=str(document.get("role", "general")),
        suppress_thinking=_suppressions(document.get("suppress_thinking"), source),
        notes=str(document.get("notes", "")),
        tags=tuple(str(tag) for tag in document.get("tags", []) or ()),
    )


def suppressed(test: BenchmarkTest, strategy: str) -> BenchmarkTest:
    """The same test, asked in a way that discourages thinking.

    Each strategy is a *prompt* change and is family-specific in practice —
    `/no_think` is a Qwen convention and inert text elsewhere — which is why the
    engine tries them and keeps whichever produces an answer rather than
    deciding from a model's name. §6 forbids asserting anything about a build
    from its name, and that applies to how it is prompted as much as to what it
    can do.
    """
    if strategy == "no_think_suffix":
        return replace(test, prompt=f"{test.prompt.rstrip()} /no_think")
    if strategy == "direct_system":
        instruction = "Answer directly. Do not think step by step."
        return replace(
            test, system=f"{test.system}\n{instruction}" if test.system else instruction
        )
    raise InvalidConfigurationError(
        f"unknown thinking suppression {strategy!r}", strategy=strategy
    )


def _suppressions(raw: Any, source: str) -> tuple[str, ...]:
    """Which suppressions to try, defaulting to all of them."""
    if raw is None:
        return THINKING_SUPPRESSIONS
    if raw in (False, "none", []):
        return ()
    if not isinstance(raw, list):
        raise InvalidConfigurationError(
            f"{source}: suppress_thinking must be a list of "
            f"{', '.join(THINKING_SUPPRESSIONS)}, or `none`", path=source
        )
    unknown = sorted(set(map(str, raw)) - set(THINKING_SUPPRESSIONS))
    if unknown:
        raise InvalidConfigurationError(
            f"{source}: unknown thinking suppression(s): {', '.join(unknown)}", path=source
        )
    return tuple(str(name) for name in raw)


def _parse_tests(raw: Any, source: str) -> tuple[BenchmarkTest, ...]:
    if not isinstance(raw, list) or not raw:
        raise InvalidConfigurationError(
            f"{source} must list at least one test under `tests`", path=source
        )
    tests = []
    for index, entry in enumerate(raw):
        mapping = _mapping(entry, source, f"tests[{index}]")
        _refuse_unknown(mapping, _TEST_KEYS, source, f"tests[{index}]")
        prompt = mapping.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise InvalidConfigurationError(
                f"{source}: tests[{index}] has no prompt", path=source
            )
        tests.append(
            BenchmarkTest(
                id=_text(mapping.get("id"), source, f"tests[{index}].id"),
                version=_version(mapping.get("version", 1)),
                prompt=prompt,
                system=mapping.get("system"),
                generation=_parse_generation(mapping.get("generation", {}), source, index),
            )
        )
    return tuple(tests)


def _parse_generation(raw: Any, source: str, index: int) -> GenerationConfig:
    mapping = _mapping(raw, source, f"tests[{index}].generation")
    _refuse_unknown(mapping, _GENERATION_KEYS, source, f"tests[{index}].generation")
    stop = mapping.get("stop") or ()
    return GenerationConfig(
        temperature=float(mapping.get("temperature", 0.0)),
        max_tokens=_whole_number(mapping.get("max_tokens", 256), source, "max_tokens"),
        top_p=float(mapping["top_p"]) if "top_p" in mapping else None,
        stop=tuple(str(item) for item in ([stop] if isinstance(stop, str) else stop)),
    )


def _refuse_unknown(mapping: Mapping[str, Any], allowed: set[str],
                    source: str, where: str) -> None:
    """Refuse a key this engine cannot honour, rather than dropping it.

    The failure mode this prevents is quiet and expensive: a specification
    carrying `evaluator:` would run as a plain performance measurement, and its
    output would be read as though the responses had been graded.
    """
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise InvalidConfigurationError(
            f"{source}: {where} has settings this engine cannot honour: "
            f"{', '.join(unknown)}. Nothing is silently ignored — remove them, or "
            "wait for the milestone that implements them (evaluators are M18, "
            "Runtime Sets M9, sweeps M17).",
            path=source, unknown=unknown,
        )


def _mapping(value: Any, source: str, where: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InvalidConfigurationError(
            f"{source}: {where} must be a mapping, not {type(value).__name__}", path=source
        )
    return value


def _text(value: Any, source: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidConfigurationError(f"{source}: {where} is required", path=source)
    return value.strip()


def _version(value: Any) -> str:
    """Versions are text, always — see this module's docstring."""
    return str(value).strip()


def _whole_number(value: Any, source: str, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidConfigurationError(
            f"{source}: {where} must be a whole number", path=source
        )
    return value


def _one_of(value: Any, allowed: tuple[str, ...], source: str, where: str) -> str:
    if value not in allowed:
        raise InvalidConfigurationError(
            f"{source}: {where} must be one of {', '.join(allowed)}", path=source
        )
    return str(value)
