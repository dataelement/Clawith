# Execution dependency composition

This package connects typed owner services to executable Tool adapters. It is application composition, not an owner or Runtime core. Import owner contracts only through `public.py`; no ORM, repositories, private codecs, compatibility paths or second application factory belong here.

`resources.py` constructs application-owned HTTP/storage resources and typed services for the single application lifespan. Only this file may construct concrete storage adapters; Tool adapters continue through Workspace's public contract. Resource cleanup must run on partial initialization, cancellation and failure, before business database disposal. S3 advisory locks use a separate explicitly configured session-pinned pool.

Adapters validate model JSON and translate public results. Workspace owns paths, authorization, revisions and publication. Tool owns Definitions, authorized bindings, exposure and scheduling. Inject trusted scopes and fixed Skill discovery; never construct Tenant, Agent or Run authority from model arguments.

Owners and Runtime must not import this package. Application composition constructs the adapters and injects executable bindings into their consumers. Lifecycle resources remain owned by the application; individual Tool calls do not close shared clients or pools.
