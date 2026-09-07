# Workspace owner

`public.py` exposes trusted scoped file operations and controlled Skill management. `files.py` owns path and result bounds; `skills.py` owns package validation, publication and loading; repositories and ORM records remain private. Workspace uses only the injected object-storage base contract, never concrete adapters or raw filesystem APIs.

Storage content and revisions commit together. Ordinary writes use explicit expected revisions, and Copy captures the checked source version. Move reports destination publication separately when source removal conflicts or is uncertain. No filesystem I/O holds a business database transaction. Audit observes committed changes and never governs outcomes.

Directory inspection captures a bounded observed manifest, not a simultaneous multi-file snapshot. Directory delete and move check its revision, mutate each captured file conditionally, preserve changed/new entries and return explicit partial outcomes. Ordinary directory cleanup uses only `rmdir_if_empty`, never unconditional recursive deletion. Move destinations must be absent and non-overlapping; verify the copied manifest before removing source files. Bounds are 128 members, 16 MiB of captured file content, 16 nested levels and 256 adapter pages.

Only Agent Workspaces expose Skills, through controlled publication rather than ordinary writes. Preparation is unpublished, package pointers change only after all members validate, and resource-scoped adapter locks protect readers against replacement cleanup across processes. Run discovery fixes Skill identities; explicit loads resolve current content. Memory remains one explicitly edited file per subject.

Catalog-backed discovery requires the injected `enabled_skill_sources` read port. It reuses the discovery transaction and filters only new discovery; explicit loads of an existing Run's Skill identities do not poll Catalog enablement. Shared refresh changes package content without rebinding Agent-private forks.

The controlling contract is [execution dependencies](../../../../specs/backend-execution-dependencies.md). Sandbox materialization and write-back are not implemented here.
