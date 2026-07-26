# Synthetic consumers

These fixtures model two independent repositories. They contain only invented
organizations, records, URLs, and decisions.

- `research-lab`: a small research group records an immutable dataset fact and
  a reproducibility decision.
- `service-team`: an operations team records a synthetic support-window fact
  and an escalation decision.

Each directory owns its configuration, memory records, state, and policy pack;
neither imports the other. The integration test copies each fixture to a
separate temporary repository, loads the installed package, and runs the
complete memory audit.
