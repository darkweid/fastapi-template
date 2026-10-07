# Event Log

`event_logs` is an append-only record of who did what: one row per action, with
the actor in its own columns, the event's own fields in a JSONB payload, and
nothing that ever gets rewritten. `src/event_log/` owns the table, the writer
and the read endpoint; every other module declares the events it records.

The row outlives the object it describes. That is the point - the answer to
"who deleted this" has to survive the deletion - and it is also the constraint
that shapes everything below: a payload cannot be pruned once written.

## Recording an Action

1. **Declare the event** in the module's `events.py`, one class per action:

   ```python
   class NoteUpdated(DomainEvent):
       code = "note.updated"
       object_type = ObjectType.NOTE

       fields: list[str]
   ```

   `code` is what lands in `event_logs.event_type`. It is prefixed with the
   package that declares it, unique across the catalog, and at most 64
   characters - `tests/unit/src/event_log/test_catalog.py` fails on all three,
   and on a class nothing records. Subclass fields become the payload; keep
   them to what a reader needs and what stays safe to hold after the object is
   gone. `ObjectType` gains a member for each entity a project starts logging.

2. **Take the actor from the request**, never from the body:

   ```python
   actor: Annotated[Actor, Depends(get_user_actor)],
   ```

   `Actor` is built only through its named constructors, which is what keeps
   one realm's type from being paired with another realm's id. A realm added
   later gets an `ActorType` member and a constructor of its own, plus a
   `get_<realm>_actor` dependency beside its `get_current_<principal>` - see
   [auth-realms.md](auth-realms.md). Use `Actor.anonymous(ip=...)` where no
   principal is proven (a failed sign-in) and `Actor.system()` for a scheduled
   task.

3. **Publish inside the use case's UoW**, before its `commit()`:

   ```python
   await uow.publish(actor, NoteUpdated(...))
   await uow.commit()
   ```

   `ApplicationUnitOfWork.publish` writes the journal row and then runs every
   subscriber, all in the action's transaction, so a rolled-back action
   leaves neither an audit row nor a subscriber's work claiming it happened.
   The row goes in a SAVEPOINT and a failed INSERT is swallowed into Sentry:
   an audit row is never worth failing the action it describes. The session
   is flushed first, so a constraint violation of the *action* surfaces to
   the caller instead of being swallowed as a logging failure. A use case
   never calls `uow.event_logs.record` itself.

4. **React to an event by subscribing to it, never by reading the journal.**
   A module that must act when something happens (a notification, an
   integration) writes `async def handler(uow, published) -> None` and lists
   it in `EVENT_SUBSCRIBERS` (`src/main/event_subscribers.py`), which both
   entry points register at import. A subscriber runs after the journal row
   whatever became of it, gets the event itself (`PublishedEvent`: `id`,
   `actor`, `event`, `occurred_at`, the same `id` and instant as the row), and
   its exception fails the action: what it writes - usually an outbox row
   carrying the event - must not be lost silently. The journal is best-effort
   by design, so anything hung on its row inherits that and loses work the
   moment an audit insert fails. An occurrence that is nobody's action and
   tells a reader of the log nothing (a reminder coming due) sets
   `journaled = False` on its class: subscribers get it, the journal does not.

Where the scenario lives in `src/core/` - the realm-agnostic `LogoutUseCase` -
the realm wraps it instead of pushing its catalog down into core:
`UserLogoutUseCase` calls the core one, then records `user.signed_out`. Core
stays free of any module's events, which is the same rule `src/core/auth/`
follows for realms.

A rejected action logs nothing, because the use case raises before it records.
Where a *rejection itself* is the interesting event - a failed sign-in - record
it and commit before raising, as `LoginUserUseCase` does; nothing else is
pending in that transaction, so the commit carries only the audit row.

The success row of a flow that hands out credentials goes last: `LoginUserUseCase`
issues the session pair and clears the throttle before it records and commits,
so a Redis outage cannot leave a durable `user.signed_in` behind an error the
caller received with no tokens.

## What Does Not Go in a Payload

`changed_fields(instance, update_data)` (`src/event_log/changes.py`) pairs each
stored value with the one about to replace it, and is the intended way to build
an update payload. Call it before the repository `update()`, which overwrites
the values in place. It drops fields whose value did not change, so an empty
result means the request changed nothing and there is nothing to log.

It also drops any field whose name contains `password`, `token`, `secret`,
`api_key` or `apikey`, matched as a substring: an audit row outlives the account, so a hash
or a token in one is a leak with no expiry. Anything written by hand is subject
to the same rule - the helper only guards the path that goes through it.

Values are optional. `NoteUpdated` stores field *names*, because the note's text
is already in `notes` and copying it here would turn the log into a version
history nothing prunes. Store the before/after pair where the previous value is
the thing a reader needs and cannot recover.

## Reading the Log

`GET /v1/event-logs/` returns the newest rows first, filtered by actor, object,
event type and a created-date range, and is gated by `Permission.VIEW_LOGS`.
The log holds every other user's actions, so reading it is a right of its own
rather than something an admin role happens to include.

Actors come back as bare `actor_type` + `actor_id`. Resolving them into names
costs one query per identity store a project has, and only the project knows
how many that is: add a use case of your own over `EventLogRepository` when a
screen needs names.

## The Table

No foreign keys, no unique constraints, no `updated_at`, no soft delete. A
foreign key would tie the row's lifetime to the entity it describes, and either
constraint would block partitioning by `created_at` later, when this is the
largest table in the database. `event_type` is a string rather than a database
enum, because the catalog gains members with every feature and the value never
comes from a request.

Four indexes end in `created_at, id` after the columns each one filters by.
Both are NOT NULL, so `ListQuery` asks for `created_at DESC, id DESC` without
`NULLS LAST`, and a plain index scanned backwards returns exactly that order. An
index without that trailing pair makes the planner sort the whole table to
answer one page;
`tests/integration/src/event_log/test_event_log.py` pins that the default page
needs no sort node.
