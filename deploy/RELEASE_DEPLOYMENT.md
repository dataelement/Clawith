# Production release deployment

Merging an automated `release/vX.Y.Z` pull request publishes the GitHub Release
and then deploys that exact tag to production. GitHub Actions does not build or
upload application artifacts. The production server fetches the tag in its
source checkout, builds versioned backend and frontend images, points the
deployment's `latest` image tags to them, and restarts the existing Compose
project. Success requires the API and worker containers to use the newly built
backend image, the frontend container to use the newly built frontend image,
and `/api/health` to report both `status: ok` and the released version.

The deployment keeps the server's ignored `.env`, `ss-nodes.json`,
`backend/agent_data`, and Docker volumes in place. It refuses to overwrite
tracked local changes. If the build, restart, or health check fails after the
checkout, it checks out the previous commit and rebuilds the previous version.

## GitHub production configuration

Configure these under **Settings > Environments > production**. Environment
protection rules can require approval before the deployment job starts.

### Variables

| Name | Current production value |
| --- | --- |
| `CLAWITH_DEPLOY_HOST` | `82.156.53.84` |
| `CLAWITH_DEPLOY_USER` | `qinrui` |
| `CLAWITH_DEPLOY_PORT` | `10022` |
| `CLAWITH_DEPLOY_PATH` | `clawith_new` |
| `CLAWITH_SOURCE_PATH` | `Clawith` |

### Secrets

| Name | Value |
| --- | --- |
| `CLAWITH_DEPLOY_SSH_KEY` | Private key for a dedicated deployment identity |
| `CLAWITH_DEPLOY_KNOWN_HOSTS` | Verified OpenSSH `known_hosts` entry for the production server |

The SSH key must be authorized for the deployment user and should be dedicated
to GitHub Actions. Verify the server fingerprint through a trusted channel
before storing the `known_hosts` entry; do not blindly trust an `ssh-keyscan`
result.

## Server prerequisites

The source directory must be a Git checkout whose `origin` points to this
repository. The deployment directory keeps the production `.env`, Compose
file, Nginx configuration, and other environment-specific files. The server
also needs:

- Docker with the Compose plugin
- `curl`
- read access to the repository
- permission for the deployment user to run Docker directly or with
  passwordless `sudo`
- an existing `.env` in `clawith_new`

Validate the server once before enabling automatic deployments:

```bash
ssh clawith
cd Clawith
git status --short
git remote -v
cd ../clawith_new
test -f .env
docker compose config --quiet
docker compose ps
curl -fsS http://127.0.0.1:3008/api/health
```

The server checkout is intentionally left at a detached release tag after a
successful deployment. Never move or reuse a published tag; publish a new
version to roll forward.
