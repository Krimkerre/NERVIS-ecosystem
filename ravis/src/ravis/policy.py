"""Application policy: what an identity may reach, and what that excludes (§9.6, §14).

Policy is a **hard** filter, and that is the whole point of the module. §14's
rule 14 says privacy constraints can never be overridden by score, so these
exclusions are applied where the pool invariants are — before ranking, not as a
penalty inside it. A preference that merely disfavours a provider is not a
privacy boundary; it is a suggestion that loses to a big enough score.

Three inputs decide a request's policy, and they are combined rather than
chosen between:

* the identity's configured level, resolved from its credential (§9.6.0),
* what the request itself declared, which may only *tighten* — see below,
* the pool's own invariants, which already exist and are untouched here.

**A request may tighten policy and may never loosen it.** The spec's ladder
(§14) is ordered by permissiveness, and it does not say in which direction a
request may move along it. Both readings are defensible on the text; only one
is safe. If a request could loosen, then `LOCAL_ONLY` is advice — anything able
to set a field can opt out of the boundary it exists to enforce. So the
effective level is the stricter of the two, always, and this comment is here
because the rule was chosen rather than read.
"""

from __future__ import annotations

import fnmatch
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ravis.core.capabilities import ModelCapabilities
from ravis.core.pools import direct_target, is_pool_id
from ravis.cost import BudgetBand
from ravis.credentials import config_directory


class PrivacyLevel(str, Enum):
    """§14's four levels, ordered from most permissive to most restrictive.

    A `str` enum so a level survives a round trip through JSON and a config
    file without a converter, and so a route explanation can print it.
    """

    NORMAL = "NORMAL"
    LOCAL_PREFERRED = "LOCAL_PREFERRED"
    TRUSTED_PROVIDERS = "TRUSTED_PROVIDERS"
    LOCAL_ONLY = "LOCAL_ONLY"

    @property
    def rank(self) -> int:
        """Position on the ladder. Higher is stricter."""
        return _LADDER.index(self)

    def strictest(self, other: PrivacyLevel) -> PrivacyLevel:
        """The tighter of two levels — the only way they are ever combined."""
        return self if self.rank >= other.rank else other


_LADDER = (
    PrivacyLevel.NORMAL,
    PrivacyLevel.LOCAL_PREFERRED,
    PrivacyLevel.TRUSTED_PROVIDERS,
    PrivacyLevel.LOCAL_ONLY,
)


@dataclass(frozen=True)
class RoutingPolicy:
    """Everything policy contributes to one routing pass.

    Frozen for the reason `ClientApplication` is: a policy that can be edited
    after resolution is a confused deputy waiting to happen. Built once per
    request, from the identity and the request, and then only read.

    The defaults are deliberately the empty policy — no privacy restriction, no
    allow-list, nothing denied. An unconfigured deployment must route exactly as
    it did before this module existed, or M16 becomes a silent behaviour change
    for everyone who never asked for one.
    """

    privacy: PrivacyLevel = PrivacyLevel.NORMAL
    # `None` and the empty set are different answers, so this is not a plain
    # frozenset. `None` means "this identity declares no allow-list", which
    # permits every provider; an *empty* allow-list means "no provider is
    # permitted", which is a coherent thing to configure and must not silently
    # read as "all of them".
    allowed_providers: frozenset[str] | None = None
    denied_providers: frozenset[str] = frozenset()
    # Glob patterns, matched against the model id — the same vocabulary
    # `ModelFilters` already uses, so an operator learns one syntax.
    excluded_models: tuple[str, ...] = ()
    # Which providers count as trusted under `TRUSTED_PROVIDERS`. Empty means
    # none are, which makes that level equivalent to local-only rather than to
    # unrestricted — the safe reading of an unconfigured trust list.
    trusted_providers: frozenset[str] = frozenset()
    # §9.6.1's declared background call. Never inferred: see `background_marked`.
    background: bool = False
    # §14's budget band, when a budget is configured. It reaches policy rather
    # than the engine directly because the last band is a *hard* exclusion and
    # every hard exclusion in this service is applied in one place — a budget
    # that blocked candidates somewhere else would be a second, quieter policy.
    budget_band: BudgetBand = BudgetBand.NORMAL
    budget_hard: bool = False
    # A marker that was present and *not* honoured, because the identity may not
    # declare one. Recorded rather than discarded so §9.7's explanation can say
    # so — see `describe`. Found live: an anonymous caller marked a request as
    # background, was routed to a paid provider exactly as §9.6.1 requires, and
    # the explanation said `requirements: none`. Correct, and silent about the
    # one thing the caller had asked for.
    background_declined: bool = False

    @property
    def local_only(self) -> bool:
        """Whether policy forbids leaving this machine outright."""
        return self.privacy is PrivacyLevel.LOCAL_ONLY

    @property
    def over_budget(self) -> bool:
        """Whether a *hard* budget has been exhausted (§14).

        Only `hard` blocks. §14 says paid APIs are blocked *if hard*, and a
        figure this service insists is an estimate should not become an outage
        because nobody said it could.
        """
        return self.budget_hard and self.budget_band is BudgetBand.EXHAUSTED

    @property
    def prefers_cheap(self) -> bool:
        """Whether the budget says to lean cheaper without refusing anything.

        Covers §14's two middle bands. They differ in the *strength* of the
        preference, and RAVIS has one ordering rather than a weighted score —
        so both are expressed the same way and the band is reported so a reader
        can see which one is in force rather than inferring it from a route.
        """
        return self.budget_band in (BudgetBand.PREFER_CHEAPER, BudgetBand.STRONG_PENALTY)

    @property
    def prefers_local(self) -> bool:
        """Whether local should win ties without excluding anything.

        `LOCAL_PREFERRED` is the one level on the ladder that is a preference
        rather than a boundary, so it is reported separately and never reaches
        the exclusion pass.
        """
        return self.privacy is PrivacyLevel.LOCAL_PREFERRED

    def describe(self) -> list[str]:
        """The policy as explanation lines (§9.7).

        Only what actually constrains this request. A line saying "privacy:
        NORMAL, no providers denied" on every explanation trains the reader to
        skip the section that matters on the one request where it does.
        """
        lines = []
        if self.privacy is not PrivacyLevel.NORMAL:
            lines.append(f"privacy level {self.privacy.value}")
        if self.allowed_providers is not None:
            allowed = ", ".join(sorted(self.allowed_providers)) or "none"
            lines.append(f"providers limited to {allowed}")
        if self.denied_providers:
            lines.append(f"providers denied: {', '.join(sorted(self.denied_providers))}")
        if self.excluded_models:
            lines.append(f"models excluded: {', '.join(self.excluded_models)}")
        if self.background:
            lines.append("declared a background call (§9.6.1)")
        if self.budget_band is not BudgetBand.NORMAL:
            lines.append(
                f"budget band {self.budget_band.value}"
                + (" — paid providers blocked" if self.over_budget else " (§14)")
            )
        if self.background_declined:
            lines.append(
                "background marker ignored: §9.6.1 honours it only from an "
                "authenticated identity, so this routed and is billed as ordinary work"
            )
        return lines


def policy_exclusions(
    policy: RoutingPolicy,
    candidates: Mapping[str, ModelCapabilities],
    *,
    provider_of: Callable[[str], str],
    remote: frozenset[str],
) -> dict[str, list[str]]:
    """Why policy refuses each candidate it refuses, keyed by model.

    Returns *all* reasons per model rather than the first, matching
    `PoolRequirements.unmet_by` — §9.7 wants an explanation someone can act on,
    and fixing one of three refusals changes nothing.

    A model policy permits simply does not appear. Callers merge this into the
    pool's own exclusions, so a reader sees one list of why-nots rather than
    having to know which subsystem rejected what.
    """
    refused: dict[str, list[str]] = {}
    for model in sorted(candidates):
        reasons = _refusals(policy, model, provider_of(model), model in remote,
                            candidates[model])
        if reasons:
            refused[model] = reasons
    return refused


def _refusals(
    policy: RoutingPolicy,
    model: str,
    provider: str,
    is_remote: bool,
    known: ModelCapabilities,
) -> list[str]:
    """Every policy reason this one model cannot serve this one request.

    Split by rule family rather than written as one pass, because the families
    are genuinely independent: a model can be both outside the allow-list and
    excluded by pattern, and reporting one of those hides the other from
    whoever has to fix it.
    """
    reasons = _privacy_refusals(policy, provider, is_remote)
    reasons += _provider_refusals(policy, provider)
    reasons += [
        f"excluded by policy pattern {pattern!r}"
        for pattern in policy.excluded_models
        if fnmatch.fnmatch(model, pattern)
    ]
    if policy.background and _is_paid(is_remote, known):
        reasons.append(_BACKGROUND_REFUSAL.format(provider=provider))
    if policy.over_budget and _is_paid(is_remote, known):
        reasons.append(
            f"the hard budget for this period is spent, and {provider} is not known to be free "
            f"(§14) — the figure behind that is an estimate, which is why only a budget "
            f"marked hard blocks"
        )
    return reasons


def _privacy_refusals(policy: RoutingPolicy, provider: str, is_remote: bool) -> list[str]:
    """The privacy ladder's two *hard* rungs.

    `LOCAL_PREFERRED` is deliberately absent: it is a preference, and putting it
    here would turn a tie-break into a refusal. `NORMAL` restricts nothing.
    """
    if not is_remote:
        return []  # nothing on the ladder constrains a model already on this machine
    if policy.privacy is PrivacyLevel.LOCAL_ONLY:
        return [f"policy is {PrivacyLevel.LOCAL_ONLY.value} and {provider} is not this machine"]
    untrusted = provider not in policy.trusted_providers
    if policy.privacy is PrivacyLevel.TRUSTED_PROVIDERS and untrusted:
        return [f"{provider} is not a trusted provider"]
    return []


def _provider_refusals(policy: RoutingPolicy, provider: str) -> list[str]:
    """The allow-list and the deny-list, which are not the same control.

    Both are reported when both apply. A deployment that denies a provider *and*
    leaves it off the allow-list has said the same thing twice, and being told
    once would leave the other in place after the first is fixed.
    """
    reasons = []
    if policy.allowed_providers is not None and provider not in policy.allowed_providers:
        reasons.append(f"{provider} is not in this application's allowed providers")
    if provider in policy.denied_providers:
        reasons.append(f"{provider} is denied to this application")
    return reasons


# Worded to say *why* rather than just what, because this is the exclusion most
# likely to surprise: a client marks a title-generation call as background and
# then asks why its frontier model was not used.
_BACKGROUND_REFUSAL = (
    "declared a background call, and {provider} is not known to be free "
    "(§9.6.1: background calls do not select paid providers by default)"
)


def _is_paid(is_remote: bool, known: ModelCapabilities) -> bool:
    """Whether using this model plausibly costs money.

    **Unknown counts as paid, and that asymmetry is the point.** Most remote
    catalogues publish no pricing at all — OpenAI's, Google's and Anthropic's
    each carry none — so `price_per_million` is `None` for the very providers a
    background call most needs to avoid. Reading absence as free would make
    §9.6.1's gate hold only for providers that happened to publish a price.

    Local models are free because the hardware is already paid for, which is the
    same fact `price_per_million == 0.0` records for them; a remote model that
    genuinely publishes a zero price is taken at its word.
    """
    if not is_remote:
        return False
    return known.price_per_million != 0.0


def background_marked(metadata: Mapping[str, Any]) -> bool:
    """Whether the request *declared* itself a background call (§9.6.1).

    Reads one explicit field and nothing else. §9.6.1 forbids inferring the
    class from prompt shape, and the reason is asymmetric: a missed background
    call costs money, while a wrongly-inferred one silently downgrades real work
    — so the only safe inference is none.

    Accepts the boolean `true` alone. A string "true" is *not* accepted: a
    client that sends the wrong type has a bug, and quietly coercing it means
    the same bug sends real work to a cheap model the day the field is spelled
    differently.
    """
    return metadata.get("background") is True


def effective_policy(
    identity_policy: RoutingPolicy,
    *,
    metadata: Mapping[str, Any],
    may_declare_background: bool,
    ceiling: PrivacyLevel,
    budget_band: BudgetBand = BudgetBand.NORMAL,
    budget_hard: bool = False,
) -> RoutingPolicy:
    """Combine what the identity carries with what the request asked for.

    Privacy moves in one direction only — see the module docstring. `ceiling` is
    the identity's `max_privacy_level`, the most *permissive* posture it may
    operate at, so the identity's own floor is applied first and the request may
    then tighten further.

    The background marker is dropped rather than refused when the identity may
    not declare one. §9.6.1 makes it a trust boundary that buys cost relief and
    nothing else, so an unauthorised marker is not an attack to reject — it is a
    claim with no privilege behind it, and the request is ordinary work.

    **Dropped, and said out loud.** The drop is recorded in
    `background_declined`, because a caller that asked for cheap routing and got
    a paid provider is owed the reason. Silently ignoring it is defensible
    security and indefensible explanation — §9.7 asks every route to answer
    "why this model", and "you were not allowed to ask for another" is the
    answer here.
    """
    declared = _declared_privacy(metadata)
    privacy = identity_policy.privacy.strictest(ceiling)
    if declared is not None:
        privacy = privacy.strictest(declared)
    return RoutingPolicy(
        privacy=privacy,
        allowed_providers=identity_policy.allowed_providers,
        denied_providers=identity_policy.denied_providers,
        excluded_models=identity_policy.excluded_models,
        trusted_providers=identity_policy.trusted_providers,
        background=may_declare_background and background_marked(metadata),
        background_declined=background_marked(metadata) and not may_declare_background,
        budget_band=budget_band,
        budget_hard=budget_hard,
    )


def _declared_privacy(metadata: Mapping[str, Any]) -> PrivacyLevel | None:
    """The privacy level a request asked for, if it asked for a valid one.

    An unrecognised value is ignored rather than raised on. It can only be
    ignored *safely* because a declaration may only tighten: the fallback is the
    identity's own level, which is never looser than what policy already
    guarantees. Were requests able to loosen, this would have to refuse.
    """
    asked = metadata.get("privacy")
    if not isinstance(asked, str):
        return None
    try:
        return PrivacyLevel(asked.upper())
    except ValueError:
        return None


@dataclass(frozen=True)
class ApplicationPolicies:
    """Policy per application id, with a default for everything unlisted.

    A mapping rather than a lookup on `ClientApplication` so policy can be
    edited without reissuing credentials, which is the whole reason §9.6 keys it
    to the identity instead of embedding it in one.
    """

    by_application: dict[str, RoutingPolicy] = field(default_factory=dict)
    default: RoutingPolicy = RoutingPolicy()

    def for_application(self, application_id: str) -> RoutingPolicy:
        return self.by_application.get(application_id, self.default)


class PolicyConfigurationError(ValueError):
    """A policy file that cannot be read as policy.

    Its own error type because the handling differs from every other config
    file here: this one is not allowed to fail open. See `load_policies`.
    """


def load_policies(
    path: Path | None = None, environment: dict[str, str] | None = None
) -> ApplicationPolicies:
    """Read `policies.json`, or the empty policy when there is none.

    **A malformed file raises rather than yielding "no policy", and that is the
    opposite of what `providers.json` does.** The rule there is written down and
    correct for that file: an operator whose provider toggles got corrupted
    should find their providers working. It is exactly wrong here. A corrupted
    policy file that quietly reads as "nothing is restricted" turns `LOCAL_ONLY`
    off without telling anyone, which is the one failure §14 rule 14 exists to
    prevent.

    So this fails closed, by refusing to produce a policy at all. `serve` lets
    that stop startup — a gateway enforcing a privacy boundary it could not read
    is worse than one that does not start — while `doctor` catches it and prints
    it as a row, because the command you run *because* configuration is broken
    must not be the one that dies on it.

    An **absent** file is not malformed. It means no policy is configured, which
    is a legitimate and common state, and yields the empty policy.
    """
    location = path or (config_directory(environment) / "policies.json")
    try:
        with location.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return ApplicationPolicies()
    except (OSError, ValueError) as failure:
        raise PolicyConfigurationError(f"{location} could not be read: {failure}") from failure
    if not isinstance(payload, dict):
        raise PolicyConfigurationError(f"{location} must contain an object")
    applications = payload.get("applications") or {}
    if not isinstance(applications, dict):
        raise PolicyConfigurationError(f"{location}: 'applications' must be an object")
    return ApplicationPolicies(
        by_application={
            str(name): _policy_from(record, f"{location}: applications.{name}")
            for name, record in applications.items()
        },
        default=_policy_from(payload.get("default") or {}, f"{location}: default"),
    )


def _policy_from(record: Any, where: str) -> RoutingPolicy:
    """One policy object, with every unrecognised value refused.

    Unknown keys and bad types raise instead of being skipped. A typo in a
    privacy level must not read as "no privacy level" — that is the fail-open
    behaviour this whole module is arranged to avoid, and a policy file is
    small enough that being strict about it costs an operator nothing.
    """
    if not isinstance(record, dict):
        raise PolicyConfigurationError(f"{where} must be an object")
    unknown = set(record) - _POLICY_KEYS
    if unknown:
        raise PolicyConfigurationError(f"{where}: unrecognised {sorted(unknown)}")
    return RoutingPolicy(
        privacy=_level(record.get("privacy"), where),
        allowed_providers=_optional_set(record.get("allowed_providers"), where),
        denied_providers=frozenset(_strings(record.get("denied_providers"), where)),
        excluded_models=tuple(_strings(record.get("excluded_models"), where)),
        trusted_providers=frozenset(_strings(record.get("trusted_providers"), where)),
    )


_POLICY_KEYS = {
    "privacy",
    "allowed_providers",
    "denied_providers",
    "excluded_models",
    "trusted_providers",
}


def _level(value: Any, where: str) -> PrivacyLevel:
    if value is None:
        return PrivacyLevel.NORMAL
    try:
        return PrivacyLevel(str(value).upper())
    except ValueError as failure:
        known = ", ".join(level.value for level in _LADDER)
        raise PolicyConfigurationError(f"{where}: unknown privacy {value!r}; expected {known}") \
            from failure


def _strings(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PolicyConfigurationError(f"{where}: expected a list of strings")
    return list(value)


def _optional_set(value: Any, where: str) -> frozenset[str] | None:
    """`None` when the key is absent, which is not the same as an empty list.

    An absent allow-list permits every provider; an empty one permits none. Both
    are things an operator may mean, so the difference has to survive the file.
    """
    if value is None:
        return None
    return frozenset(_strings(value, where))


def policy_refusals(
    policy: RoutingPolicy,
    candidates: Mapping[str, ModelCapabilities],
    *,
    addressed: str,
    provider_of: Callable[[str], str],
    remote: frozenset[str],
) -> dict[str, list[str]]:
    """Policy refusals for the candidate set *and* for what the client addressed.

    The addressed model needs its own pass because it is frequently not in
    `candidates`: a direct address to a translated provider names a model that
    only that provider's catalogue contains, and a pool id is not a model at
    all. Without this, `ravis/google/<model>` would have been the one shape of
    request that policy could not see — which is exactly the shape someone
    reaching around a policy would use.
    """
    refused = policy_exclusions(policy, candidates, provider_of=provider_of, remote=remote)
    if addressed and addressed not in refused and addressed not in candidates:
        target = direct_target(addressed) or addressed
        if not is_pool_id(addressed):
            reasons = _refusals(
                policy,
                target,
                provider_of(addressed),
                # Known-remote, or unknown to this catalogue and therefore
                # assumed off this machine. Unknown counts as remote for the
                # same reason unknown counts as paid: the other default lets an
                # unrecognised model through the one check meant to stop it, and
                # the models that reach here are precisely the ones a local
                # catalogue cannot vouch for.
                target in remote or target not in candidates,
                candidates.get(target, ModelCapabilities(model_id=target)),
            )
            if reasons:
                refused[addressed] = reasons
    return refused
