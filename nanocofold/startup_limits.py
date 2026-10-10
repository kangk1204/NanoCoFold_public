"""Budgets for native-library readiness, separate from analysis budgets."""

# Measured first-use imports on a new HDD-backed host exceeded 90 seconds;
# even an engine's --help took 36 seconds. Every native readiness path needs
# the same bounded allowance. An enclosing execution budget still takes priority.
READINESS_TIMEOUT_SECONDS = 300
