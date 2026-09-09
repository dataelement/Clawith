# Workspace owner

Shared Memory distillation is restricted to non-preview Agent-owned Main execution. Reject Membership/Group scopes at the service mutation boundary, not only Tool exposure. See [Memory distillation](../../../../.agents/notes/implemented/architecture/2026-09-08-agent-owned-memory-distillation.md) for the superseded private-context exception and provenance limitation.

Captured `allow_shared_file_writes=False` rejects Agent `files/` mutations at the common path boundary, including directory operations. It grants no other output space. A2A receiver results use request-owned temporary files and return through the sender's authorized Workspace; see the [continuation amendment](../../../../specs/backend-product-input-continuations.md).

`public.py` exposes trusted scoped file operations and controlled Skill management. `files.py` owns path and result bounds; `skills.py` owns package validation, publication and loading; repositories and ORM records remain private. Workspace uses only the injected object-storage base contract, never concrete adapters or raw filesystem APIs.

Storage content and revisions commit together. Ordinary writes use explicit expected revisions, and Copy captures the checked source version. Move reports destination publication separately when source removal conflicts or is uncertain. No filesystem I/O holds a business database transaction. Audit observes committed changes and never governs outcomes.

Directory inspection captures a bounded observed manifest, not a simultaneous multi-file snapshot. Directory delete and move check its revision, mutate each captured file conditionally, preserve changed/new entries and return explicit partial outcomes. Ordinary directory cleanup uses only `rmdir_if_empty`, never unconditional recursive deletion. Move destinations must be absent and non-overlapping; verify the copied manifest before removing source files. Bounds are 128 members, 16 MiB of captured file content, 16 nested levels and 256 adapter pages.

Only Agent Workspaces expose Skills, through controlled publication rather than ordinary writes. Preparation is unpublished, package pointers change only after all members validate, and resource-scoped adapter locks protect readers against replacement cleanup across processes. Run discovery fixes Skill identities; explicit loads resolve current content. Memory remains one explicitly edited file per subject.

Catalog-backed discovery requires the injected `enabled_skill_sources` read port. It reuses the discovery transaction and filters only new discovery; explicit loads of an existing Run's Skill identities do not poll Catalog enablement. Shared refresh changes package content without rebinding Agent-private forks.

The controlling contract is the [Workspace Memory amendment](../../../../specs/backend-workspace-memory-scope.md), which retains the execution-dependencies baseline outside its explicit distillation restriction. Sandbox materialization and write-back are not implemented here.
