# Release deployment status

Release deployment is unavailable during G002. The target branch provides only a local health-only Backend and does not have a product schema, Frontend entry, migration path, worker topology, or supported container/Kubernetes release.

The former Drone tag, migration, deploy, and upgrade flows have been removed and replaced with Backend validation gates. Retained deployment configuration is quarantined and uses the isolated `clawith_target` namespace. Do not publish images, restart production services, run Alembic, or claim deployment readiness from these files. G009 must approve and verify the complete deployment, recovery, and live-acceptance contract before this document can contain release procedures.
