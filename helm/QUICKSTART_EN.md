# Helm status

G002 supports only the local health-only Backend; Kubernetes product deployment is unavailable. `helm/clawith/values.yaml` defaults `g002Deferred: true`, so every resource template emits no Kubernetes resource.

Do not use Helm install, upgrade, backup, or restore procedures as current operations. A later Goal must approve the target schema, product entry, and deployment contract before removing the quarantine, and the database namespace must remain `clawith_target`. See [README.md](../README.md) for current local validation.
