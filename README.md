# Clawith

Clawith is undergoing a clean-break Backend rewrite. The current `develop` branch is at G002: it provides the target application composition, validated configuration, database-resource lifecycle, and one health endpoint. Product APIs, Agent Runtime execution, authentication, the Frontend, migrations, and production deployment are not available from this target yet.

## Current G002 entry

Requirements:

- Python 3.12 or newer
- `uv`
- PostgreSQL 15 or newer with `psql` and `createdb`

Prepare the target Backend:

```bash
bash setup.sh
```

`setup.sh` synchronizes `backend/.env` from `backend/.env.example`, preserves supported values and explicit target connection credentials, and installs Backend dependencies. For the isolated local default it creates the `clawith_target` role and database only when absent; it never resets an existing role password. Explicit operator-managed target connections skip PostgreSQL changes. Legacy or incomplete URLs are rejected before environment or database mutation. It does not read or create a repository-root `.env`, run Alembic, create tables, install checkpoints, seed data, repair schemas, or start product services.

Start the health-only Backend:

```bash
bash restart.sh
curl http://127.0.0.1:8008/api/health
```

`restart.sh` requires `backend/.env`, starts exactly one `app.main:app` Uvicorn worker, and succeeds only after `/api/health` responds. It does not start Docker, the Frontend, workers, connectors, migrations, or legacy Runtime processes.

## Configuration and database boundary

The target Settings owner reads only `backend/.env`. Its default and local setup database is `postgresql+asyncpg://clawith_target:clawith_target@localhost:5432/clawith_target`. The repository-root `.env.example` is not a Backend configuration template.

The checked-in Alembic revision chain remains frozen legacy evidence until G008 replaces it with the approved target baseline. G002 startup scripts never invoke Alembic. Migration commands are explicit operator actions only and must not be run against `clawith_target` until the target baseline is implemented and reviewed.

## Docker, CI/CD, and Helm

The Docker Compose, CI/CD, deploy, and Helm files are retained for namespace validation and later deployment work. They are not a supported G002 product-start path. Every Compose service is quarantined behind the `deferred-product` profile, and Helm defaults `g002Deferred` to true so it renders no resources. Current CI runs Backend gates instead of migration, release, deployment, or upgrade workflows. Do not treat an explicitly overridden deferred configuration as evidence that the target product is available.

## Development checks

Run Backend checks from `backend/`:

```bash
uv run --extra dev pytest
uv run --extra dev ruff check app tests
uv run --extra dev pyright app
```

See [backend/AGENTS.md](backend/AGENTS.md) for the current architecture and rewrite rules, and [docs/testing.md](docs/testing.md) for evidence boundaries.

## License

[Apache 2.0](LICENSE)
