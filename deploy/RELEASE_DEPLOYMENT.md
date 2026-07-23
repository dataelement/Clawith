# Production release deployment

Merging an automated `release/vX.Y.Z` pull request publishes the GitHub Release
and then deploys that exact tag to production. GitHub Actions does not build or
upload application artifacts. The production server fetches the tag and runs
`docker compose up -d --build` in its existing source checkout.

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

The deployment directory must already be a Git checkout whose `origin` points
to this repository. The server also needs:

- Docker with the Compose plugin
- `curl`
- read access to the repository
- permission for the deployment user to run Docker directly or with
  passwordless `sudo`
- an existing `.env` and `ss-nodes.json` in `clawith_new`

Validate the server once before enabling automatic deployments:

```bash
ssh clawith
cd clawith_new
git status --short
git remote -v
test -f .env
test -f ss-nodes.json
docker compose config --quiet
docker compose up -d --build
curl -fsS http://127.0.0.1:3008/api/health
```

The server checkout is intentionally left at a detached release tag after a
successful deployment. Never move or reuse a published tag; publish a new
version to roll forward.
