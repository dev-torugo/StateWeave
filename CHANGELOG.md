# Changelog

## Unreleased

- Began local clean-room extraction.
- Defined the memory-core vertical-slice contract.
- Integrated official Draft 2020-12 validation into configuration, audit,
  mutation, and migration verification.
- Added optional workflow, orchestration, runtime, telemetry, adapter, and
  policy-pack modules with synthetic contract tests.
- Added a pinned Linux, macOS, and Windows CI matrix for Python 3.11–3.13.
- Recorded source-available licensing intent without applying a license.
- Authorized an initial public GitHub development snapshot while retaining the
  definitive-license, trademark, release, and package-publication gates.
- Pinned the verified Ruff version so the public CI cannot silently widen its
  lint contract when a new tool release appears.
- Made configuration schema validation precede platform-specific path
  resolution so POSIX and Windows traversal inputs fail with the same public
  configuration error.
