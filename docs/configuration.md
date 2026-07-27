# Configuration

Every consumer owns a versioned `stateweave.toml`. Core code contains no fixed
repository directories, authority role, TTL, classification, or operational
limit.

The parsed TOML document must satisfy the packaged Draft 2020-12 configuration
schema before it is returned to callers. Cross-field path topology and TTL
rules remain explicit semantic checks.

## Version

```toml
schema_version = 1
```

Unknown versions fail closed. Configuration schema versions are independent
from package and record schema versions.

## Project identity

```toml
[project]
id = "example-project"
name = "Example Project"
```

The ID is a stable machine key. The display name may change without changing
record identifiers.

## Paths

```toml
[paths]
facts = "memory/facts"
decisions = "memory/decisions"
state = "memory/state/current.json"
metadata = ".stateweave"
backups = ".stateweave/backups"
migrations = ".stateweave/migrations"
extensions = ".stateweave/extensions"
```

All paths must be relative and remain inside the project root after symlink and
normalization resolution. Absolute paths and `..` traversal fail closed.
`backups`, `migrations`, and `extensions` must be non-overlapping children of
`metadata`. The generic extensions path lets optional modules persist
project-owned data without making memory-core import those modules. Older
schema-version-1 configurations that omit `extensions` resolve it to
`<metadata>/extensions`; newly rendered configurations write it explicitly.

## TTL classes

```toml
[memory]
default_fact_class = "general"
no_expiry_classes = ["immutable"]

[memory.ttl_days]
volatile = 30
general = 90
policy = 180
```

For a verified expiring fact, the effective due date is the explicit
`review_after` or `verified_at + ttl_days[fact_class]`. With
`enforce_ttl_ceiling = true`, an explicit date cannot extend the configured
TTL. A no-expiry class may omit `review_after`.

## Roles

```toml
[roles]
allowed = ["maintainer", "reviewer", "contributor"]
```

Facts and state use `owner_role`; decisions use `decider_role`. Core validates
membership but assigns no special meaning to a particular role name.

## Limits

```toml
[limits]
max_record_bytes = 262144
max_records = 10000
max_state_items = 200
lock_timeout_seconds = 5.0
lock_stale_after_seconds = 900
```

Stale lock age is diagnostic. Expiration alone never transfers ownership or
deletes a lock. `lock-status` returns the owner digest and token required by
the explicitly confirmed `recover-lock` operation. `lock_timeout_seconds`
bounds both acquisition and retry of transient release-time sharing
violations. Waiting writers inspect owner evidence only when the acquisition
deadline expires; other release failures still fail closed.

## Policy

```toml
[policy]
allowed_classifications = ["public", "internal"]
review_warning_days = 14
enforce_ttl_ceiling = true
fail_on_stale_verified = true
require_reciprocal_supersession = true
detect_structured_conflicts = true
```

Changing policy changes subsequent audit behavior. It does not rewrite
records. Policy changes should therefore be reviewed and versioned by the
consumer repository.
