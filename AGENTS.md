# Clawith Project Instructions

This file is the project-level entry point for agent instructions.

## Primary Source of Project Rules

For this repository, the canonical project instructions live under:

- `.agents/rules/`
- `.agents/workflows/`

When working in this project, read and follow those files first. If this file and a file under `.agents/` ever conflict, prefer the more specific file under `.agents/`.

## Required Read Order

At the start of work on Clawith, use this order:

1. `.agents/workflows/read_architecture.md`
2. Relevant files under `.agents/rules/`

In practice:

- For general design, implementation, or feature questions, read `.agents/rules/design_and_dev.md`
- For deployment and environment updates, read `.agents/rules/deploy.md`
- For GitHub-related work, read `.agents/rules/github.md`
- For versioning and release work, read `.agents/rules/release.md`

## Notes

- The architecture document currently present in this repository is `ARCHITECTURE_SPEC_EN.md`
- Do not invent alternative instruction filenames when the real rules already exist under `.agents/`

## Local Collaboration Preference

- At the end of each completed task, play a short local completion sound on the user's machine (for example with `afplay` on macOS) so the user notices work has finished even when reading or working in another window.
- This reminder should be treated as a default behavior for this repository across sessions unless the user explicitly asks to skip it for a specific task.

## Server Development / Deployment Environment

- At the start of every Clawith session, read this section so the deployment target is available in context.
- Primary development server: `root@192.168.106.163`
- Server repository path: `/home/work/Clawith`
- Standard deployment flow after local changes are committed and pushed:
  1. SSH to the server: `ssh root@192.168.106.163`
  2. Enter the repo: `cd /home/work/Clawith`
  3. Pull the latest code from git.
  4. Pull compose images: `docker compose -f deploy/docker-compose.yml -p clawith pull`
  5. Rebuild services: `docker compose -f deploy/docker-compose.yml -p clawith build`
  6. Restart in detached mode: `docker compose -f deploy/docker-compose.yml -p clawith up -d`
