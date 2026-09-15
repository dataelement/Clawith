# Helm 状态说明

当前 G002 仅支持本地 health-only Backend，不支持 Kubernetes 产品部署。`helm/clawith/values.yaml` 默认设置 `g002Deferred: true`，所有资源模板都不会输出 Kubernetes 资源。

不要把 Helm install、upgrade 或旧数据库备份恢复流程作为当前操作。必须等后续 Goal 批准目标 schema、产品入口和部署合同后，才能解除隔离；数据库命名必须保持为 `clawith_target`。当前本地验证见 [README.md](../README.md)。
