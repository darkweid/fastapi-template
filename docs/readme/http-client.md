# Outgoing HTTP

Every call to a third-party API goes through `HttpClient` (`src/core/http/client.py`). Never open an `aiohttp.ClientSession` of your own, and never one per call: a session per request pays a TLS handshake each time, which is most of a short call's latency.

## Building a provider client

The provider client takes an `HttpRequester` and knows only its own API. Its factory builds the `HttpClient` once per process - cache it, or every call opens a pool of its own (the process logs a warning at every 50th open client):

```python
@lru_cache
def get_payments_client() -> PaymentsClient:
    payments_config = config.payments
    return PaymentsClient(
        http=HttpClient(
            name="payments",
            base_url=payments_config.PAYMENTS_BASE_URL,
            user_agent=get_user_agent(),
            headers={"Authorization": f"Bearer {payments_config.PAYMENTS_TOKEN}"},
            timeout=HttpTimeout(total_seconds=payments_config.PAYMENTS_TIMEOUT_SECONDS),
            retry=RETRY_CONNECT_ONLY,
        )
    )
```

and calls it with an operation name, which is what logs and errors show:

```python
response = await self._http.request("POST", "/payments", operation="payments.create", json=body)
if is_permanent_failure(response.status): ...
```

- **`base_url`** is an `http(s)` origin with an optional path prefix, no `?` or `#`; anything else raises `ValueError` when the client is built. `path` starts with `/` and is appended to it.
- **`User-Agent`** is required and is set on the client only (`user_agent=`); one in `headers`, of the client or of a request, raises `ValueError`. `get_user_agent()` returns `HTTP_USER_AGENT`, or `<project-name>/<VERSION>` when that is blank. WAFs often refuse the library default.
- **Body.** `json=` sends JSON, `data=` a form (a mapping) or raw `bytes`/`str`; not both. `json=None` sends no body, so a JSON `null` body goes as `data=b"null"` with its `Content-Type`. Multipart and streamed uploads are not supported. `params=` takes a mapping or a list of pairs, the list for a repeated key (`[("id", 1), ("id", 2)]`); a value is a `str` or an `int`, never a `bool` (write `"true"` as the API spells it).
- **The body** is read before `request` returns and capped at `HttpLimits.max_response_bytes` of decoded bytes while it streams; the `Content-Length` header is not trusted either way. `response.text()` decodes with the declared charset, UTF-8 otherwise.
- **A status never raises.** The caller reads it: `response.ok`, `is_permanent_failure(status)`, `response.json_or_none()` for a body that may be an HTML error page.
- **Redirects are not followed.** A 3xx comes back as a status: following it would re-send the body and any custom key header to the host the `Location` names.
- **`HttpTransportError` means no status came back.** Its message is `<operation>: <exception class>` and never the URL, so a token in a path stays out of logs and Sentry. `request_sent` is False only when nothing was written to a connection - a refused connection, a DNS or TLS failure, a deadline spent waiting for a pooled connection - so repeating it cannot duplicate anything. `transient` is False for a TLS failure (a certificate, a broken handshake), a malformed URL and an answer over the size cap; `is_transient(error)` is that classification for a caller that sorts aiohttp errors of its own.
- **Closing.** Every client opened in the process is closed by `close_http_clients()` at API and worker shutdown, so an integration has no shutdown hook of its own. A session belongs to the event loop that opened it: using the client from another running loop while that session is open raises `RuntimeError`; a session whose loop has already closed is dropped with a warning and a new one opened; a client used after the hook opens a new session. Closing works from anywhere a running loop can reach the owner: on the owner itself, through `run_coroutine_threadsafe` when the owner runs in another thread; a close while the owner sits idle raises `RuntimeError` and leaves the client open for its own loop to close.

## Choosing a retry policy

| Policy | Repeats | Use for |
|---|---|---|
| `None` (default) | nothing | calls whose caller retries (a taskiq task with `retry_on_error`, a batch loop) |
| `RETRY_CONNECT_ONLY` | exchanges that never reached the server | any method, POST included |
| `RETRY_IDEMPOTENT` | network errors and 408/429/500/502/503/504, honouring `Retry-After` | GET, PUT, DELETE |
| `replace(RETRY_IDEMPOTENT, allow_non_idempotent=True)` | the same, for POST | only an API that deduplicates by an idempotency key |

A policy that may repeat a sent request refuses a non-idempotent method with `ValueError` before anything is sent. Pass `retry=` per request to override the client's policy, and `retry=None` to switch it off.

Below any policy, aiohttp itself resends a request of an idempotent method once when its connection drops (`ServerDisconnectedError`, `ClientOSError`), the RFC 9112 allowance for a keep-alive connection the server closed. That resend is the library's, not the policy's: `None` does not switch it off, and it is never applied to POST or PATCH.

## Testing

`tests/fakes/http.py` has `FakeHttpClient`: hand it to the provider client, queue `fake_response(...)` answers or exceptions, and assert on `requests`. It refuses what the real client refuses - a relative path, a `User-Agent` in `headers`, a repeating policy on a non-idempotent method, both `json` and `data`, a `bool` query value; build it with the factory's policy (`FakeHttpClient(retry=...)`) so that choice is checked too. The policy is validated, not executed: the fake answers each call once, and records the per-request `retry` and `timeout` it was given.

Unit tests close every client after each test (`tests/unit/conftest.py`), so a client cached across tests reopens in the next test's loop.
