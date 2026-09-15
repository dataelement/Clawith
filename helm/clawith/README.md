# Clawith Helm chart quarantine

This chart is not a supported G002 product deployment. The target currently exposes only a local health-only Backend, and `values.yaml` defaults `g002Deferred` to true so every resource template is suppressed.

Do not install or upgrade this chart as a product path. The retained values and templates exist for namespace and structural validation only. A later Goal must approve the target schema, product entry, lifecycle, secrets, storage, and deployment contract before the quarantine can be removed. Any future database value must remain `clawith_target`.
