"""The credential store, connected to the things that send requests.

M10 built the store — the 0600 file, the Keychain read, the precedence order,
and a `Secret` that refuses to print itself. What it never built was a caller.
`resolve()` was reached only by `status()`, so a key typed into the Credentials
screen was written, reported as configured, and never sent to anything. Every
adapter went on reading its own settings field.

That is a worse failure than a missing feature, because the screen answered
truthfully about the part it could see. It had stored the key.
"""

from __future__ import annotations

import httpx
import pytest

from ravis.config import Settings
from ravis.credentials import CredentialStore, credential_for
from ravis.transparent import build_transparents
from ravis.upstream import Upstream
from ravis.upstreams import api_root_for, default_base_url


def a_store(tmp_path, environment=None) -> CredentialStore:
    store = CredentialStore(allow_environment=True, environment=environment or {})
    store._file = type(store._file)(tmp_path / "credentials.json")
    return store


# ── The store reaches a request now ────────────────────────────────────────


def test_a_stored_credential_is_what_an_adapter_sends(tmp_path) -> None:
    store = a_store(tmp_path)
    store.store("google", "typed-into-the-screen")

    assert credential_for(store, "google", "") == "typed-into-the-screen"


def test_the_environment_still_works_for_deployments_that_predate_the_screen(
    tmp_path,
) -> None:
    """M10 said environment variables keep working. They have to keep working.

    A deployment written before the Credentials screen supplies its keys through
    the environment, and connecting the store must not be the change that
    silently stops those requests.
    """
    store = a_store(tmp_path, {"RAVIS_GOOGLE_API_KEY": "from-the-environment"})

    assert credential_for(store, "google", "") == "from-the-environment"


def test_the_most_recent_explicit_action_wins(tmp_path) -> None:
    """File over environment, which is the order the store already documents.

    Somebody who types a key into the screen while an old environment variable
    is still set means the one they just typed.
    """
    store = a_store(tmp_path, {"RAVIS_GOOGLE_API_KEY": "old"})
    store.store("google", "just-typed")

    assert credential_for(store, "google", "") == "just-typed"


def test_a_settings_field_is_the_fallback_and_not_the_winner(tmp_path) -> None:
    store = a_store(tmp_path)
    assert credential_for(store, "google", "from-settings") == "from-settings"

    store.store("google", "from-the-store")
    assert credential_for(store, "google", "from-settings") == "from-the-store"


# ── Google, which turned out not to need an adapter ────────────────────────


def test_google_roots_its_api_somewhere_openai_does_not() -> None:
    """The whole of what "supporting Google" required.

    The expected shape was a translating adapter like `anthropic.py` — several
    hundred lines and a wire format to track. Google publishes an
    OpenAI-compatible surface, so the difference is two strings.
    """
    upstream = Upstream(
        base_url=default_base_url("google"), api_key="k", api_root=api_root_for("google")
    )

    assert upstream.api_url("/chat/completions") == (
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    )
    assert upstream.api_url("/models") == (
        "https://generativelanguage.googleapis.com/v1beta/openai/models"
    )


def test_a_native_path_is_never_versioned() -> None:
    """`url_for` stays literal, and that is the reason `api_url` is separate.

    Ollama reads `/api/show`. Applying an API root to it would break the two
    upstreams this project actually runs on, to support one it does not yet.
    """
    upstream = Upstream(base_url="http://127.0.0.1:11434", api_key="")

    assert upstream.url_for("/api/show") == "http://127.0.0.1:11434/api/show"
    assert upstream.api_url("/models") == "http://127.0.0.1:11434/v1/models"


def test_a_google_upstream_needs_no_base_url_and_finds_its_key(tmp_path) -> None:
    """Declaring a hosted provider should not mean copying a URL out of docs.

    That is a string nobody can verify by reading it, and getting it subtly
    wrong produces a 404 that reads as an outage.
    """
    store = a_store(tmp_path)
    store.store("google", "AIza-not-a-real-key")
    settings = Settings(upstreams='[{"name": "gemini", "kind": "google"}]')

    built = build_transparents(settings, httpx.AsyncClient(), store)

    upstream = built["gemini"].upstream
    assert upstream.base_url == "https://generativelanguage.googleapis.com"
    # Found by *kind*, though the upstream was named `gemini` — the screen seeds
    # a row called `google` and an operator should not have to know that the two
    # names must match.
    # `key()` rather than `api_key`: the field holds what the declaration wrote,
    # and the method is what a request actually authenticates with — resolved
    # each time, so typing a key takes effect on the next request.
    assert upstream.key() == "AIza-not-a-real-key"
    assert upstream.api_url("/chat/completions").endswith("/v1beta/openai/chat/completions")


def test_the_upstream_name_beats_the_kind(tmp_path) -> None:
    """Two Google upstreams on different keys is a real arrangement.

    Only the name distinguishes them, so the name is looked up first.
    """
    store = a_store(tmp_path)
    store.store("google", "the-shared-one")
    store.store("work", "the-work-one")
    settings = Settings(
        upstreams='[{"name": "work", "kind": "google"}, {"name": "home", "kind": "google"}]'
    )

    built = build_transparents(settings, httpx.AsyncClient(), store)

    assert built["work"].upstream.key() == "the-work-one"
    assert built["home"].upstream.key() == "the-shared-one"


def test_a_key_typed_after_startup_reaches_the_next_request(tmp_path) -> None:
    """The half of this that a settings screen lives or dies on.

    Credentials were read once while building the upstream, so a key entered on
    the Credentials screen did nothing until RAVIS restarted — and nothing said
    so. The store is consulted per request now, which is the rule
    `ProviderState` already follows for the enable toggle.
    """
    store = a_store(tmp_path)
    settings = Settings(upstreams='[{"name": "google", "kind": "google"}]')

    built = build_transparents(settings, httpx.AsyncClient(), store)
    upstream = built["google"].upstream
    assert upstream.key() == ""

    # What the screen's Save button does, after everything was already built.
    store.store("google", "typed-just-now")

    assert upstream.key() == "typed-just-now"


def test_the_credentials_screen_says_which_providers_can_be_reached() -> None:
    """A key stored for a provider nothing can route to is still going nowhere.

    Connecting the store fixes half the problem: the key now reaches an adapter
    *if one exists*. `routable` is the other half — the difference between "not
    configured" and "configured and going nowhere", which is §4.1's
    advertise-when rule applied to a settings page.
    """
    from fastapi.testclient import TestClient

    from ravis.app import create_app

    settings = Settings(upstreams='[{"name": "gemini", "kind": "google"}]')
    with TestClient(create_app(settings)) as client:
        items = client.get("/api/v1/providers/credentials").json()["items"]

    by_name = {item["name"]: item for item in items}
    assert by_name["google"]["label"] == "Google AI Studio"
    assert by_name["google"]["routable"] is True
    # Declared nowhere, so a key typed against it would go nowhere and the
    # screen can say so instead of implying otherwise.
    assert by_name["openrouter"]["routable"] is False


def test_a_local_runtime_still_has_to_say_where_it_is() -> None:
    """No default address for LM Studio or Ollama, deliberately.

    They are wherever the operator started them, and guessing a port is how a
    dashboard reports the wrong machine as healthy.
    """
    from ravis.upstreams import UpstreamConfigurationError, upstream_specs

    settings = Settings(upstreams='[{"name": "local", "kind": "lmstudio"}]')

    with pytest.raises(UpstreamConfigurationError, match="no base_url"):
        upstream_specs(settings)


# ── What actually goes on the wire ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_request_google_would_receive(tmp_path) -> None:
    """The strongest check available without a key, and it is worth being
    explicit that it is not the same as calling Google.

    It asserts the request RAVIS composes: the URL, the method, and the bearer
    token taken from the store. What it cannot assert is that Google accepts it
    — that needs a real key, which belongs to whoever runs this and not in a
    test.
    """
    seen: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "gemini-2.5-flash"}]})

    store = a_store(tmp_path)
    store.store("google", "AIza-not-a-real-key")
    settings = Settings(upstreams='[{"name": "google", "kind": "google"}]')
    client = httpx.AsyncClient(transport=httpx.MockTransport(capture))

    built = build_transparents(settings, client, store)
    models = await built["google"].adapter.models()

    assert models == ["gemini-2.5-flash"]
    assert len(seen) == 1
    assert str(seen[0].url) == (
        "https://generativelanguage.googleapis.com/v1beta/openai/models"
    )
    assert seen[0].method == "GET"
    assert seen[0].headers["authorization"] == "Bearer AIza-not-a-real-key"
    await client.aclose()


@pytest.mark.asyncio
async def test_a_key_that_was_never_stored_sends_no_authorization(tmp_path) -> None:
    """An empty credential must not become `Bearer ` on the wire.

    A header that is present and empty reads as an authentication attempt and
    gets a 401, which is a worse diagnostic than no header at all.
    """
    seen: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    settings = Settings(upstreams='[{"name": "google", "kind": "google"}]')
    client = httpx.AsyncClient(transport=httpx.MockTransport(capture))

    built = build_transparents(settings, client, a_store(tmp_path))
    await built["google"].adapter.models()

    assert "authorization" not in seen[0].headers
    await client.aclose()


# ── Who may call this API from a browser ───────────────────────────────────


def _cors(origin: str, settings: Settings | None = None):
    """One credentials read, as a browser on `origin` would make it.

    RAVIS already had all of this — the allowlist, the headers, and a written
    reason why credentials use PUT (it always preflights, so a page that was
    never allow-listed cannot slip a write through as a simple request). What it
    had was an empty default, justified as "correct until a dashboard is
    actually served". One is served now.
    """
    from fastapi.testclient import TestClient

    from ravis.app import create_app

    with TestClient(create_app(settings or Settings())) as client:
        return client.get("/api/v1/providers/credentials", headers={"Origin": origin})


def test_the_dashboard_may_call_this_api() -> None:
    """Without this the credential screen cannot reach RAVIS at all.

    A page on one port calling a service on another is cross-origin by the
    browser's definition, so every request failed preflight and the screen fell
    back silently to invented data.
    """
    response = _cors("http://localhost:8790")

    assert response.headers["access-control-allow-origin"] == "http://localhost:8790"


def test_both_spellings_of_loopback_are_allowed() -> None:
    """They are different origins to a browser, and which one appears depends
    on what the operator typed into the address bar."""
    assert _cors("http://127.0.0.1:8790").headers["access-control-allow-origin"] == (
        "http://127.0.0.1:8790"
    )


def test_a_request_with_no_origin_is_untouched() -> None:
    """No Origin header means no browser made this request.

    Command-line clients and SDKs do not set one, and they are the ordinary
    caller — the allowlist must not turn into an authentication check.
    """
    from fastapi.testclient import TestClient

    from ravis.app import create_app

    with TestClient(create_app(Settings())) as client:
        assert client.get("/api/v1/providers/credentials").status_code == 200


def test_another_local_port_may_not() -> None:
    """The reason this is an exact allowlist rather than a loopback pattern.

    This API stores provider credentials. Allowing any local origin would let
    any page the operator happens to visit write one — the browser handing a
    stranger a local write primitive. A named port is a boundary; "localhost"
    is not one.
    """
    refused = _cors("http://localhost:3000")

    assert refused.status_code == 403
    assert "access-control-allow-origin" not in refused.headers


def test_a_remote_origin_may_not() -> None:
    refused = _cors("https://evil.example")

    assert refused.status_code == 403
    assert "access-control-allow-origin" not in refused.headers


def test_no_ambient_authority_is_offered_across_origins() -> None:
    """RAVIS authenticates with a header, never a cookie, so there is no
    ambient authority for a cross-origin page to borrow."""
    response = _cors("http://localhost:8790")

    assert response.headers.get("access-control-allow-credentials") != "true"
