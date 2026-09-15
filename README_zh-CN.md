# Clawith

当前 `develop` 分支处于后端 clean-break 重写的 G002 health-only 阶段，只提供目标配置、数据库资源生命周期和 `/api/health` 健康检查。产品 API、Agent Runtime、认证、前端、迁移和生产部署尚不可用。

本地验证请按 [README.md](README.md) 操作：`setup.sh` 只准备 `backend/.env` 和 `clawith_target` 数据库，`restart.sh` 只启动一个健康检查 Backend。Docker、Helm、发布、升级和 Alembic 都不是当前产品启动路径；必须等待后续 Goal 的明确合同。
