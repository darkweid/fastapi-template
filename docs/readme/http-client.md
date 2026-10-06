# Outgoing HTTP

Every call to a third-party API goes through `HttpClient` (`src/core/http/client.py`). Never open an `aiohttp.ClientSession` of your own, and never one per call: a session per request pays a TLS handshake each time, which is most of a short call's latency.

## Building a provider client

The provider client takes an `HttpRequester` and knows only its own API. Its factory builds the `HttpClient`:

```python
def build_payments_client(payments_config: PaymentsConfig) -> PaymentsClient:
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

- **`User-Agent`** is required. `get_user_agent()` returns `HTTP_USER_AGENT`, or `<project-name>/<VERSION>` when that is blank. WAFs often refuse the library default.
- **A status never raises.** The caller reads it: `response.ok`, `is_permanent_failure(status)`, `response.json_or_none()` for a body that may be an HTML error page.
- **Redirects are not followed.** A 3xx comes back as a status: following it would re-send the body and any custom key header to the host the `Location` names.
- **`HttpTransportError` means no status came back.** Its message is `<operation>: <exception class>` and never the URL, so a token in a path stays out of logs and Sentry. `request_sent` says whether the server may have seen the request; a deadline that runs out, waiting for a pooled connection included, counts as sent.
- **Closing.** Every client opened in the process is closed by `close_http_clients()` at API and worker shutdown, so an integration has no shutdown hook of its own.

## Choosing a retry policy

| Policy | Repeats | Use for |
|---|---|---|
| `None` (default) | nothing | calls whose caller retries (a taskiq task with `retry_on_error`, a batch loop) |
| `RETRY_CONNECT_ONLY` | exchanges that never reached the server | any method, POST included |
| `RETRY_IDEMPOTENT` | network errors and 408/429/500/502/503/504, honouring `Retry-After` | GET, PUT, DELETE |
| `replace(RETRY_IDEMPOTENT, allow_non_idempotent=True)` | the same, for POST | only an API that deduplicates by an idempotency key |

A policy that may repeat a sent request refuses a non-idempotent method with `ValueError` before anything is sent. Pass `retry=` per request to override the client's policy, and `retry=None` to switch it off.

## Testing

`tests/fakes/http.py` has `FakeHttpClient`: hand it to the provider client, queue `fake_response(...)` answers or exceptions, and assert on `requests`.
