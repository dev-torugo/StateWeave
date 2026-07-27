# Versioning and compatibility

StateWeave separates four version axes:

1. package version;
2. project configuration schema;
3. record schema;
4. migration and optional-module contract versions.

The current package version is `0.0.0.dev0`. This is a development identifier,
not a beta or stable maturity claim.

## Compatibility rules

- Unknown configuration or record schema versions fail closed.
- A reader may preserve a historical version only when that version has an
  explicit registered schema and migration policy.
- A migration never infers missing historical evidence.
- Every applied migration records source and target versions, input/output
  hashes, backup reference, status, and timestamps.
- Package changes that remove a supported schema or alter a public API require
  an explicit compatibility note and migration path.

## Versioned CI matrix

No operating system is officially supported until the clean-install,
filesystem, migration, restore, and complete test gates run there.

The checked-in CI matrix targets Python 3.11–3.13 on:

- Linux;
- macOS;
- Windows.

The workflow runs contracts and tests in all nine combinations, with a separate
Linux job for formatting, lint, typing, the canonical gate, and distribution
build. Actions are pinned to immutable revisions and checkout does not persist
credentials.

The public development repository has hosted evidence for the publication
snapshot: Linux and macOS matrix jobs passed, while the first Windows run found
and led to a path-validation ordering fix. That evidence is historical and
does not cover the current uncommitted continuity changes. No operating system
is therefore declared officially supported until the current revision runs the
complete hosted matrix.
