from pydantic import ConfigDict, Field
import pytest

from src.core.cache.decorators import cached
from src.core.cache.interface import CacheKey
from src.core.cache.memory_cache import InMemoryCache
from src.core.cache.runtime import get_cache_instance, reset_cache, set_cache
from src.core.cache.serializer import JsonSerializer
from src.core.schemas import Base


class Summary(Base):
    name: str


class AliasedSummary(Base):
    model_config = ConfigDict(populate_by_name=True)

    full_name: str = Field(alias="fullName")


@pytest.fixture(autouse=True)
def runtime_cache() -> InMemoryCache:
    cache = InMemoryCache(
        serializer=JsonSerializer(), default_ttl=60, version_ttl=604800
    )
    set_cache(cache)
    yield cache
    reset_cache()


def summary_key(user_id: int, **_: object) -> CacheKey:
    return CacheKey(namespace=f"user:{user_id}", suffix="summary")


async def test_second_call_does_not_reach_the_function() -> None:
    calls: list[int] = []

    @cached(key_builder=summary_key, ttl=60)
    async def load(user_id: int) -> Summary:
        calls.append(user_id)
        return Summary(name="ada")

    first = await load(1)
    second = await load(1)

    assert first == second == Summary(name="ada")
    assert calls == [1]


async def test_key_builder_receives_the_same_arguments() -> None:
    seen: list[tuple[object, ...]] = []

    def builder(*args: object, **kwargs: object) -> CacheKey:
        seen.append((args, tuple(sorted(kwargs.items()))))
        return CacheKey(namespace="user:1", suffix="summary")

    @cached(key_builder=builder, ttl=60)
    async def load(user_id: int, *, verbose: bool = False) -> Summary:
        return Summary(name="ada")

    await load(7, verbose=True)

    assert seen == [((7,), (("verbose", True),))]


async def test_different_keys_do_not_share_values() -> None:
    @cached(key_builder=summary_key, ttl=60)
    async def load(user_id: int) -> Summary:
        return Summary(name=f"user-{user_id}")

    assert (await load(1)).name == "user-1"
    assert (await load(2)).name == "user-2"


async def test_invalidated_namespace_recomputes() -> None:
    values = iter(["ada", "grace"])

    @cached(key_builder=summary_key, ttl=60)
    async def load(user_id: int) -> Summary:
        return Summary(name=next(values))

    assert (await load(1)).name == "ada"

    await get_cache_instance().invalidate("user:1")

    assert (await load(1)).name == "grace"


async def test_function_without_return_annotation_is_rejected() -> None:
    with pytest.raises(TypeError, match="return annotation"):

        @cached(key_builder=summary_key, ttl=60)
        async def load(user_id: int):  # type: ignore[no-untyped-def]
            return None


async def test_aliased_field_round_trips_through_the_cache(
    runtime_cache: InMemoryCache,
) -> None:
    @cached(key_builder=summary_key, ttl=60)
    async def load(user_id: int) -> AliasedSummary:
        return AliasedSummary(full_name="Ada Lovelace")

    first = await load(1)
    second = await load(1)

    assert first == second == AliasedSummary(full_name="Ada Lovelace")
    # Proves the stored payload uses the alias, not the field name - which is
    # what a response_model with the same alias would re-validate against.
    raw = await runtime_cache.get_raw(CacheKey(namespace="user:1", suffix="summary"))
    assert raw is not None
    assert '"fullName"' in raw
    assert "full_name" not in raw
