# Alembic during G002

G002 is health-only and has no target schema baseline. The checked-in revision files are frozen legacy evidence until G008 and must not be edited, applied to `clawith_target`, or presented as a supported upgrade path.

Backend startup, `setup.sh`, `restart.sh`, CI, Docker, and Helm never run Alembic. A future target migration is an explicit operator-only action after the baseline contract is approved. Its environment must use validated `app.infrastructure.config.Settings`, the shared `app.infrastructure.database.Base.metadata`, and the exact `clawith_target` namespace. See [alembic/AGENTS.md](alembic/AGENTS.md) for the governing migration rules.
