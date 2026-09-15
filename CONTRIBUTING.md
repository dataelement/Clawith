# Contributing during G002

The current `develop` branch is a health-only clean-break Backend skeleton. Contributions must preserve the ownership and verification rules in [AGENTS.md](AGENTS.md) and [backend/AGENTS.md](backend/AGENTS.md).

Use [README.md](README.md) for the supported local setup. Backend configuration belongs only in `backend/.env`, and every target database URL must name `clawith_target`. Do not create a root `.env` or treat the retained Frontend, Docker Compose, Helm, deployment, or legacy Alembic files as current product entrypoints.

Run the Backend checks documented in [docs/testing.md](docs/testing.md). Schema changes remain unavailable until the reviewed target baseline; G002 contributions must not run or document automatic migration, seed, checkpoint, repair, deployment, or upgrade flows.
