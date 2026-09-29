class ScrubbedError(Exception):
    """Stands in for an exception whose message echoes user data.

    A driver's IntegrityError carries the PostgreSQL DETAIL (`Key (email)=(...)`,
    `Failing row contains (...)`) and a pydantic ValidationError prints every
    `input_value`; both reach a log line or a Sentry event through `str(exc)`
    and through the exception chain. This one carries a summary the caller
    built from safe fields, the original traceback, and no chain.
    """


def scrubbed(summary: str, original: BaseException) -> ScrubbedError:
    return ScrubbedError(summary).with_traceback(original.__traceback__)
