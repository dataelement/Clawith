# Production release deployment

Production releases are proposed and published by GitHub Actions, while Drone
owns CI validation, artifact transfer, and production deployment.

## Release flow

1. Manually run the `Release` GitHub Actions workflow.
2. GitHub Actions calculates the next version, updates the version files, drafts
   release notes, and opens a `release/vX.Y.Z` pull request.
3. Merging that pull request creates and pushes the annotated `vX.Y.Z` tag.
4. Drone receives the tag webhook and runs the complete pipeline:
   - build the previous and target backend/frontend images;
   - validate fresh-database migrations;
   - validate a fresh application deployment;
   - validate upgrading from the previous stable release;
   - export and transfer the target images;
   - load the images and recreate the production application services;
   - verify the proxied API health endpoint;
   - send a Feishu notification when the release succeeds or fails.
5. GitHub Actions publishes the GitHub Release and finishes without waiting for
   Drone. Drone continues the deployment asynchronously and reports its status
   on the tagged commit.

Only tags matching `refs/tags/v*` enter the Drone release pipeline. Branch
pushes and pull requests still run CI, but never export or deploy images.

## Drone configuration

The repository must be trusted by Drone because the CI steps use privileged
containers and the host Docker socket.

Configure these Drone repository secrets:

| Name | Purpose |
| --- | --- |
| `PROXY` | Optional HTTP/HTTPS proxy used during clone and image builds |
| `PRIVATE_SERVER_IP` | Production server hostname or IP address |
| `sshpwd` | Password for the production deployment user |
| `FEISHU_DEPLOY_WEBHOOK` | Feishu custom bot webhook for successful deployment notifications |

The deployment currently connects as `qinrui` on port `10022` and writes
release artifacts to `/home/qinrui/clawith_new`.

GitHub Actions no longer requires the production SSH key, known-hosts entry, or
the former `CLAWITH_DEPLOY_*` production environment variables.

## Server prerequisites

The production server must provide:

- Docker with the Compose plugin;
- permission for `qinrui` to use Docker;
- `/home/qinrui/clawith_new/.env`;
- `/home/qinrui/clawith_new/nginx/default.conf`;
- the external Docker network named by
  `CLAWITH_DOCKER_NETWORK` (default: `clawith_network`);
- existing PostgreSQL, Redis, and MinIO services reachable on that network as
  `postgres`, `redis`, and `minio`;
- `/data/agent_data` for persistent agent data.

`ss-nodes.json` is optional. If it is absent, Drone creates a safe empty JSON
array and the application starts without the optional SS/Discord proxy. An
existing real configuration is preserved.

## Deployment behavior

Drone uploads:

- `clawith-backend-new.tar`;
- `clawith-frontend-new.tar`;
- `docker-compose.cd.yml`;
- `image-tag.txt`.

The remote deployment loads the transferred images and force-recreates only
`backend-api`, `backend-worker`, and `frontend`. PostgreSQL, Redis, and MinIO
are not recreated. Deployment succeeds only after the frontend proxy returns an
API health response whose status is `ok`.

Drone sends the release result, tag, build link, and short commit SHA to the
configured Feishu custom bot. The success message is sent only after the health
check passes. Any failure during a tag pipeline, including build, test,
transfer, restart, or health-check failures, sends a failure message. A
notification API error fails the Drone step so the missing notification is
visible.

If Drone fails, the GitHub tag and Release remain published for investigation.
Fix or rerun the Drone build for that tag; do not move or reuse a published
release tag.
