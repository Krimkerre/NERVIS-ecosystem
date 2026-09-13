"""What a generated Codex schema tree says about the protocol, reduced to hashes (design §4.3).

`codex app-server generate-json-schema --out <folder> [--experimental]` writes a tree whose
`codex_app_server_protocol.schemas.json` is the **combined bundle**: every definition, plus the
three message unions — `ClientRequest`, `ServerNotification` and `ServerRequest` — whose variants
each name one method and the definition of its params (brief §9; checked on Homebrew 0.154.0).

**Why per-definition hashes.** A whole-tree hash changes on any additive release — 0.153.4 → 0.154.0
added fields and changed nothing RAVIS uses (brief §9) — so a new build's report must say *what*
changed. Each definition is hashed as canonical JSON (sorted keys, no spaces), and the report sorts
the changes into three piles:

- **used methods missing**: a request RAVIS sends, or a message it reads, that the new build no
  longer has (acceptance check 5 fails on any);
- **used definitions changed**: the params and responses of the used methods, and everything they
  refer to, transitively;
- **other definitions changed**: counted, not named.

**The experimental bundle is the one compared**, because RAVIS always initializes with
`experimentalApi: true` (`supervisor.py`), so the experimental shapes are the protocol it speaks;
the stable tree is still compared by its whole-tree hash.

A response has no link to its method in the bundle, so it is found by the schema's own naming:
`GetAccountParams` answers with `GetAccountResponse`. All nineteen used requests follow it in
0.154.0.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ravis.codex.pin import MESSAGE_KINDS, UsedSurface

COMBINED_BUNDLE = "codex_app_server_protocol.schemas.json"
#: Each message union, by the used-methods list that names its methods.
UNIONS = {
    "client_requests": "ClientRequest",
    "server_notifications": "ServerNotification",
    "server_requests": "ServerRequest",
}
#: Keys under `definitions` that hold more definitions rather than being one.
NAMESPACES = ("v2",)
REF_PREFIX = "#/definitions/"
RECORD_FORMAT = 1
#: Used requests whose params schema is null, so their response can't be found by name: checked on
#: Codex 0.154.0, where `account/logout` is the only one.
RESPONSES_WITHOUT_PARAMS = {"account/logout": "LogoutAccountResponse"}


@dataclass(frozen=True)
class BundleFacts:
    """One combined bundle: each method's params definition, and every definition by name."""

    methods: dict[str, dict[str, str | None]]
    definitions: dict[str, Any]

    def hashes(self) -> dict[str, str]:
        return {name: canonical_sha256(schema) for name, schema in self.definitions.items()}

    def all_methods(self) -> set[str]:
        return {method for names in self.methods.values() for method in names}

    def resolve(self, bare_name: str) -> str | None:
        """The full name of a definition the design names bare: `ThreadStartParams` → `v2/…`."""
        for candidate in (bare_name, *(f"{space}/{bare_name}" for space in NAMESPACES)):
            if candidate in self.definitions:
                return candidate
        return None

    def closure(self, roots: Iterable[str]) -> set[str]:
        """`roots` and every definition they refer to, however deep."""
        seen: set[str] = set()
        waiting = [root for root in roots if root in self.definitions]
        while waiting:
            name = waiting.pop()
            if name in seen:
                continue
            seen.add(name)
            waiting.extend(ref for ref in _refs(self.definitions[name]) if ref not in seen)
        return seen

    def response_of(self, method: str, params: str | None) -> str | None:
        """The definition a request answers with: `GetAccountParams` → `GetAccountResponse`.

        A request whose params schema is null names nothing to go by; the ones RAVIS uses are in
        `RESPONSES_WITHOUT_PARAMS`.
        """
        if params is not None and params.endswith("Params"):
            response = params.removesuffix("Params") + "Response"
            if response in self.definitions:
                return response
        bare = RESPONSES_WITHOUT_PARAMS.get(method)
        return self.resolve(bare) if bare is not None else None

    def resolved(self, used: UsedSurface) -> dict[str, dict[str, dict[str, str | None]]]:
        """Each used method this bundle has, with its params and (for requests) response names."""
        resolved: dict[str, dict[str, dict[str, str | None]]] = {}
        for kind, names in used.methods.items():
            known = self.methods.get(kind, {})
            answered = kind != "server_notifications"
            resolved[kind] = {
                method: {
                    "params": known[method],
                    "response": self.response_of(method, known[method]) if answered else None,
                }
                for method in names
                if method in known
            }
        return resolved

    def used_definitions(self, used: UsedSurface) -> set[str]:
        """The params and responses of every used method, and what they refer to."""
        roots = {
            name
            for methods in self.resolved(used).values()
            for names in methods.values()
            for name in names.values()
            if name is not None
        }
        return self.closure(roots)


@dataclass(frozen=True)
class DefinitionRecord:
    """A pinned build's per-definition hashes, and which of them RAVIS used (`schemas/*.json`)."""

    hashes: dict[str, str]
    used_definitions: frozenset[str]

    @classmethod
    def from_json(cls, raw: Mapping[str, Any] | None) -> DefinitionRecord | None:
        if raw is None or raw.get("format") != RECORD_FORMAT:
            return None
        hashes, used = raw.get("definitions"), raw.get("used_definitions")
        if not isinstance(hashes, dict) or not isinstance(used, list):
            return None
        return cls(
            hashes={str(name): str(digest) for name, digest in hashes.items()},
            used_definitions=frozenset(str(name) for name in used),
        )


def read_bundle(tree: Path) -> BundleFacts:
    """The combined bundle in a generated tree. Raises `OSError` or `ValueError` when unreadable."""
    document = json.loads((tree / COMBINED_BUNDLE).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("definitions"), dict):
        raise ValueError(f"{COMBINED_BUNDLE} has no definitions")
    definitions = _flattened(document["definitions"])
    methods = {kind: _union_methods(definitions.get(union)) for kind, union in UNIONS.items()}
    return BundleFacts(methods=methods, definitions=definitions)


def canonical_sha256(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def definition_record(
    facts: BundleFacts, used: UsedSurface, *, codex_version: str, experimental_tree: str
) -> dict[str, Any]:
    """The file a tested entry's `definitions` names, built from its experimental bundle."""
    return {
        "format": RECORD_FORMAT,
        "codex_version": codex_version,
        "experimental_tree": experimental_tree,
        "bundle": f"experimental {COMBINED_BUNDLE}",
        "used_methods": facts.resolved(used),
        "used_definitions": sorted(facts.used_definitions(used)),
        "definitions": dict(sorted(facts.hashes().items())),
    }


def missing_methods(facts: BundleFacts, used: UsedSurface) -> list[str]:
    """The used methods a bundle doesn't have (acceptance check 5)."""
    return sorted(
        method
        for kind in MESSAGE_KINDS
        for method in used.methods.get(kind, ())
        if method not in facts.methods.get(kind, {})
    )


def surface_changes(
    pinned: DefinitionRecord, found: BundleFacts, used: UsedSurface
) -> list[str]:
    """What changed in the file rules' surface (check 7a): named definitions, missing methods."""
    found_hashes = found.hashes()
    changed = []
    for bare in used.surface_definitions:
        name = found.resolve(bare) or f"v2/{bare}"
        if pinned.hashes.get(name) != found_hashes.get(name):
            changed.append(bare)
    present = found.all_methods()
    changed.extend(f"{method} missing" for method in used.surface_methods if method not in present)
    return changed


def protocol_comparison(
    pinned: DefinitionRecord | None, found: BundleFacts, used: UsedSurface
) -> dict[str, Any]:
    """The version check's `protocol` block, apart from the two tree flags the caller adds."""
    missing = missing_methods(found, used)
    if pinned is None:
        return {
            "strict_rules_surface_changed": None,
            "used_methods_missing": missing,
            "used_definitions_changed": None,
            "other_definitions_changed": None,
        }
    found_hashes = found.hashes()
    changed = {
        name
        for name in pinned.hashes.keys() | found_hashes.keys()
        if pinned.hashes.get(name) != found_hashes.get(name)
    }
    used_names = pinned.used_definitions | found.used_definitions(used)
    return {
        "strict_rules_surface_changed": bool(surface_changes(pinned, found, used)),
        "used_methods_missing": missing,
        "used_definitions_changed": sorted(_bare(name) for name in changed & used_names),
        "other_definitions_changed": len(changed - used_names),
    }


def _flattened(definitions: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for name, schema in definitions.items():
        if name in NAMESPACES and isinstance(schema, dict):
            flat.update({f"{name}/{inner}": value for inner, value in schema.items()})
        else:
            flat[name] = schema
    return flat


def _union_methods(union: object) -> dict[str, str | None]:
    """Each method in a message union, with the definition its params refer to.

    The reference may be wrapped: optional params are `{"anyOf": [{"$ref": …}, {"type": "null"}]}`
    (`account/rateLimits/read`), so the first definition named anywhere inside counts. Params that
    are only `{"type": "null"}` name none.
    """
    variants = union.get("oneOf") if isinstance(union, dict) else None
    methods: dict[str, str | None] = {}
    for variant in variants if isinstance(variants, list) else []:
        properties = variant.get("properties", {}) if isinstance(variant, dict) else {}
        enum = properties.get("method", {}).get("enum")
        if not isinstance(enum, list) or not enum or not isinstance(enum[0], str):
            continue
        named = list(_refs(properties.get("params", {})))
        methods[enum[0]] = named[0] if named else None
    return methods


def _refs(schema: Any) -> Iterable[str]:
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "$ref" and isinstance(value, str) and value.startswith(REF_PREFIX):
                yield value.removeprefix(REF_PREFIX)
            else:
                yield from _refs(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _refs(value)


def _bare(name: str) -> str:
    """`ThreadItem` for `v2/ThreadItem`: the name the design and the contract fixture use."""
    space, slash, rest = name.partition("/")
    return rest if slash and space in NAMESPACES else name
