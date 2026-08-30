

# ── Local models do not stay resident forever (§9's residency, in practice) ──


def test_a_local_request_carries_a_ttl_so_the_model_unloads_when_idle() -> None:
    """LM Studio holds a model until something evicts it, so residency piles up
    and then decides routing — this machine's own decisions read "memory is
    tight (19% free), so already-loaded models were preferred over the pool's
    usual ordering". The runtime takes `ttl` on the request and unloads that
    many seconds after last use, so RAVIS names a number instead of running an
    eviction loop."""
    import json

    from ravis.api.openai.chat import _with_model

    body = _with_model(b'{"model":"pool"}', {"model": "pool"}, "gemma", 600)

    assert json.loads(body) == {"model": "gemma", "ttl": 600}


def test_a_client_that_asked_for_its_own_ttl_keeps_it() -> None:
    """A default for requests with no opinion, not an override of one that has."""
    import json

    from ravis.api.openai.chat import _with_model

    payload = {"model": "gemma", "ttl": 30}
    body = _with_model(b'{"model":"gemma","ttl":30}', payload, "gemma", 600)

    assert json.loads(body)["ttl"] == 30


def test_no_ttl_is_sent_when_it_is_turned_off() -> None:
    """0 leaves it to the runtime, which is what an operator who set a TTL in LM
    Studio's own settings wants — and the untouched body is forwarded verbatim
    rather than rebuilt."""
    from ravis.api.openai.chat import _with_model

    original = b'{"model":"gemma","messages":[]}'

    assert _with_model(original, {"model": "gemma", "messages": []}, "gemma", 0) is original
