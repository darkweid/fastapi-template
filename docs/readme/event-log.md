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

3. **Record inside the use case's UoW**, before its `commit()`:

   ```python
   await uow.event_logs.record(uow.session, actor, NoteUpdated(...))
   await uow.commit()
   ```

   The row is part of the same transaction as the action, so a rolled-back
   action leaves no audit trail claiming it happened. `record()` writes inside
   a SAVEPOINT and swallows a failed INSERT into Sentry: an audit row is never
   worth failing the action it describes, and the SAVEPOINT is what lets the
   caller's own work survive the rejection. It flushes the session first, so a
   constraint violation of the *action* surfaces to the caller instead of being
   swallowed as a logging failure.

A rejected action logs nothing, because the use case raises before it records.
Where a *rejection itself* is the interesting event - a failed sign-in - record
it and commit before raising, as `LoginUserUseCase` does; nothing else is
pending in that transaction, so the commit carries only the audit row.

## What Does Not Go in a Payload

`changed_fields(instance, update_data)` (`src/event_log/changes.py`) pairs each
stored value with the one about to replace it, and is the intended way to build
an update payload. Call it before the repository `update()`, which overwrites
the values in place. It drops fields whose value did not change, so an empty
result means the request changed nothing and there is nothing to log.

It also drops any field whose name contains `password`, `token`, `secret` or
`api_key`, matched as a substring: an audit row outlives the account, so a hash
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

Four expression indexes carry `created_at DESC NULLS LAST, id DESC` - the order
`ListQuery` asks for - after the columns each one filters by. An index without
that trailing order makes the planner sort the whole table to answer one page;
`tests/integration/src/event_log/test_event_log.py` pins that the default page
needs no sort node.
