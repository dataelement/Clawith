# Clawith Helm 部署快速开始指南

## 📋 目录结构

```
helm/
├── clawith/                    # Helm Chart 主目录
│   ├── Chart.yaml             # Chart 元数据
│   ├── values.yaml            # 配置文件
│   ├── README.md              # 详细文档
│   └── templates/             # Kubernetes 资源模板
│       ├── _helpers.tpl       # 模板辅助函数
│       ├── namespace.yaml     # Namespace
│       ├── secrets.yaml       # 密钥
│       ├── backend.yaml       # 后端服务
│       ├── frontend.yaml      # 前端服务
│       ├── ingress.yaml       # Ingress 配置
│       ├── postgresql.yaml    # PostgreSQL 数据库
│       ├── redis.yaml         # Redis 缓存
│       └── storageclass.yaml  # 存储类（可选）
└── QUICKSTART.md              # 本文档
```

## ⚠️ 重要说明

**K8s 部署方式限制：**
- ✅ 当前 Helm Chart **仅支持 Native Agent 部署方式**
- ❌ **不支持 OpenClaw Agent 托管模式**
- 如需使用 OpenClaw Agent 托管，请采用 Docker Compose 或其他部署方式

Native Agent 是 Clawith 内置的代理模式，适用于 Kubernetes 环境。OpenClaw Agent 托管模式目前仅在 Docker Compose 环境中支持。

## 🚀 快速开始

### 1. 编辑配置文件

编辑 `helm/clawith/values.yaml`，根据你的环境修改以下配置：

```bash
vi helm/clawith/values.yaml
```

**必须修改的配置项：**

```yaml
# 1. 配置镜像仓库地址
global:
  imageRegistry: docker.io/yourusername  # 修改为你的镜像仓库地址

# 2. 配置镜像标签
backend:
  image:
    tag: latest  # 建议使用具体版本号，如 v1.0.0

frontend:
  image:
    tag: latest  # 建议使用具体版本号，如 v1.0.0

# 3. 配置存储
backend:
  persistence:
    existingClaim: ""  # 如果使用现有 PVC，填入 PVC 名称
    storageClass: ""  # 如果新建，则修改为你的 StorageClass 名称
    size: 10Gi

postgresql:
  image:
    registry: docker.io/bitnami  # 修改为你的镜像仓库
  auth:
    password: "clawith123456"  # 强烈建议修改为强密码！
  primary:
    persistence:
      existingClaim: ""  # 如果使用现有 PVC，填入 PVC 名称
      storageClass: ""  # 如果新建，则修改为你的 StorageClass 名称
      size: 8Gi

redis:
  image:
    registry: docker.io  # 修改为你的镜像仓库
  persistence:
    existingClaim: ""  # 如果使用现有 PVC，填入 PVC 名称
    storageClass: ""  # 如果新建，则修改为你的 StorageClass 名称
    size: 2Gi

# 4. 配置域名
frontend:
  ingress:
    host: "clawith.example.com"  # 修改为你的域名

# 5. 修改应用密钥（重要！）
backend:
  secrets:
    secretKey: "your-secret-key-at-least-50-characters-long"
    jwtSecretKey: "your-jwt-secret-key-at-least-32-characters"

# 6. 如果需要私签证书支持，启用 hostCerts
backend:
  hostCerts:
    enabled: false  # 如果需要则设置为 true
```

### 2. 安装

```bash
helm install clawith ./helm/clawith -n clawith --create-namespace
```

### 3. 验证部署

```bash
# 查看 Pod 状态
kubectl get pods -n clawith

# 应该看到类似输出：
# NAME                                  READY   STATUS    RESTARTS   AGE
# clawith-backend-xxx                   1/1     Running   0          2m
# clawith-frontend-xxx                  1/1     Running   0          2m
# clawith-postgresql-0                  1/1     Running   0          2m
# clawith-redis-xxx                     1/1     Running   0          2m

# 查看服务和 Ingress
kubectl get svc,ingress -n clawith
```

## 🔧 常见配置场景

### 场景 1：使用现有 PVC（已有存储）

```yaml
backend:
  persistence:
    enabled: true
    existingClaim: "clawith-agent-data-pvc"  # 你的 PVC 名称
    # 不需要指定 storageClass 和 size

postgresql:
  primary:
    persistence:
      enabled: true
      existingClaim: "clawith-postgresql-data"  # 你的 PVC 名称

redis:
  persistence:
    enabled: true
    existingClaim: "redisdata"  # 你的 PVC 名称
```

### 场景 2：创建新的 PVC（动态存储）

```yaml
backend:
  persistence:
    enabled: true
    existingClaim: ""  # 留空
    storageClass: "nfs-client"  # 你的 StorageClass 名称
    size: 10Gi

postgresql:
  primary:
    persistence:
      enabled: true
      existingClaim: ""
      storageClass: "nfs-client"
      size: 8Gi

redis:
  persistence:
    enabled: true
    existingClaim: ""
    storageClass: "nfs-client"
    size: 2Gi
```

### 场景 3：配置镜像仓库

如果使用私有镜像仓库或不同的镜像源：

```yaml
global:
  imageRegistry: registry.example.com/myproject  # 私有仓库

backend:
  image:
    repository: clawith-backend
    tag: v1.0.0  # 使用具体版本号

frontend:
  image:
    repository: clawith-frontend
    tag: v1.0.0

postgresql:
  image:
    registry: registry.example.com/bitnami
    repository: bitnami/postgresql
    tag: 15.3.0-debian-11-r7

redis:
  image:
    registry: registry.example.com
    repository: redis
    tag: 7-alpine
```

### 场景 4：启用私签证书支持

如果你的环境需要自定义 CA 证书（如企业内网环境）：

```yaml
backend:
  hostCerts:
    enabled: true
    paths:
      certs: /etc/ssl/certs
      shareCA: /usr/local/share/ca-certificates
    containerPaths:
      sslCertFile: /app/cacert.pem
      requestsCaBundle: /app/cacert.pem
      curlCaBundle: /app/cacert.pem
```

### 场景 5：使用外部数据库

如果你有独立的 PostgreSQL 和 Redis 服务：

```yaml
postgresql:
  enabled: false
  external:
    host: "postgresql.example.com"
    port: 5432
    database: clawith
    username: postgres
    password: "your-password"

redis:
  enabled: false
  external:
    host: "redis.example.com"
    port: 6379
    database: 0
    password: ""  # 如果有密码
```

### 场景 6：生产环境配置

```yaml
global:
  imageRegistry: registry.yourcompany.com/clawith

backend:
  replicaCount: 2  # 多副本
  image:
    tag: v1.0.0  # 使用固定版本
  resources:
    limits:
      cpu: 2000m
      memory: 4Gi
    requests:
      cpu: 500m
      memory: 1Gi
  persistence:
    storageClass: "ssd-storage"  # 高性能存储
    size: 50Gi

frontend:
  replicaCount: 2
  image:
    tag: v1.0.0
  ingress:
    enabled: true
    annotations:
      cert-manager.io/cluster-issuer: "letsencrypt-prod"
      nginx.ingress.kubernetes.io/ssl-redirect: "true"
    host: "clawith.yourcompany.com"
    tls:
      enabled: true
      secretName: clawith-tls-secret

postgresql:
  auth:
    password: "STRONG_PASSWORD_HERE"  # 必须使用强密码
  primary:
    persistence:
      storageClass: "ssd-storage"
      size: 20Gi
    resources:
      limits:
        cpu: 2000m
        memory: 2Gi
      requests:
        cpu: 500m
        memory: 512Mi

redis:
  persistence:
    storageClass: "ssd-storage"
    size: 5Gi
  resources:
    limits:
      cpu: 1000m
      memory: 1Gi
    requests:
      cpu: 250m
      memory: 256Mi
```

## 📝 常用命令

### 查看状态

```bash
# 查看所有资源
kubectl get all -n clawith

# 查看 Pod 状态
kubectl get pods -n clawith

# 查看 PVC
kubectl get pvc -n clawith

# 查看 Helm 发布状态
helm status clawith -n clawith

# 查看 Helm 部署的值
helm get values clawith -n clawith
```

### 查看日志

```bash
# 后端日志
kubectl logs -n clawith -l app.kubernetes.io/component=backend -f

# 前端日志
kubectl logs -n clawith -l app.kubernetes.io/component=frontend -f

# PostgreSQL 日志
kubectl logs -n clawith -l app.kubernetes.io/component=postgresql -f

# Redis 日志
kubectl logs -n clawith -l app.kubernetes.io/component=redis -f
```

### 升级

```bash
# 修改 values.yaml 后升级
helm upgrade clawith ./helm/clawith -n clawith

# 或者使用 --set 覆盖特定值
helm upgrade clawith ./helm/clawith -n clawith \
  --set backend.image.tag=v1.0.1 \
  --set frontend.image.tag=v1.0.1

# 升级镜像版本
helm upgrade clawith ./helm/clawith -n clawith \
  --set global.imageRegistry=registry.example.com/newproject
```

### 回滚

```bash
# 查看历史版本
helm history clawith -n clawith

# 回滚到上一版本
helm rollback clawith -n clawith

# 回滚到指定版本
helm rollback clawith 1 -n clawith
```

### 卸载

```bash
# 卸载应用（保留 PVC）
helm uninstall clawith -n clawith

# 如需删除 PVC
kubectl delete pvc -n clawith --all

# 删除 namespace
kubectl delete namespace clawith
```

## 🔍 访问应用

### 通过 Ingress 访问（推荐）

如果配置了 Ingress，直接通过域名访问：
```
http://clawith.example.com  # 或你配置的域名
```

### 通过 Port Forward 访问

如果没有配置 Ingress，可以使用端口转发：

```bash
# 转发前端服务
kubectl port-forward -n clawith svc/clawith-frontend 8080:80

# 然后访问 http://localhost:8080
```

```bash
# 转发后端服务（用于 API 调试）
kubectl port-forward -n clawith svc/clawith-backend 8000:8000

# 然后访问 http://localhost:8000
```

## 🛠️ 故障排查

### Pod 无法启动

```bash
# 查看 Pod 详情
kubectl describe pod <pod-name> -n clawith

# 查看日志
kubectl logs <pod-name> -n clawith

# 查看事件
kubectl get events -n clawith --sort-by='.lastTimestamp'
```

### PVC 绑定失败

```bash
# 检查 PVC 状态
kubectl get pvc -n clawith
kubectl describe pvc <pvc-name> -n clawith

# 检查 StorageClass
kubectl get storageclass

# 检查 PV
kubectl get pv
```

### 镜像拉取失败

```bash
# 检查镜像配置
helm get values clawith -n clawith | grep -A 3 image

# 查看 Pod 事件
kubectl describe pod <pod-name> -n clawith | grep -A 10 Events

# 手动拉取镜像测试
docker pull your-registry/clawith-backend:latest
```

### 数据库连接问题

```bash
# 检查 PostgreSQL 服务
kubectl get svc -n clawith | grep postgresql

# 检查数据库密码
kubectl get secret -n clawith -o yaml | grep postgres-password

# 进入后端 Pod 测试连接
kubectl exec -it -n clawith deployment/clawith-backend -- /bin/bash
# 在 Pod 内测试
nc -zv clawith-postgresql 5432
```

## 🔐 安全建议

### 1. 修改默认密码

⚠️ **重要**：在部署前必须修改所有默认密码！

```yaml
backend:
  secrets:
    secretKey: "生成一个至少 50 字符的随机字符串"
    jwtSecretKey: "生成一个至少 32 字符的随机字符串"

postgresql:
  auth:
    password: "生成一个强密码"  # 不要使用默认的 clawith123456
```

生成随机密码的方法：
```bash
# 生成 50 字符的随机字符串
openssl rand -base64 36

# 或使用 Python
python3 -c "import secrets; print(secrets.token_urlsafe(50))"

# 生成 32 字符的随机字符串
openssl rand -base64 24
```

### 2. 使用外部 Secrets

在生产环境中，建议使用外部 Secret 管理：

```bash
# 创建 Secret
kubectl create secret generic clawith-secrets \
  --from-literal=secret-key='your-secret-key' \
  --from-literal=jwt-secret-key='your-jwt-secret' \
  -n clawith

# 在 values.yaml 中配置
secrets:
  create: false
  existingSecret: "clawith-secrets"
```

### 3. 启用 HTTPS

```yaml
frontend:
  ingress:
    enabled: true
    annotations:
      cert-manager.io/cluster-issuer: "letsencrypt-prod"
      nginx.ingress.kubernetes.io/ssl-redirect: "true"
    host: "clawith.yourcompany.com"
    tls:
      enabled: true
      secretName: clawith-tls-secret
```

### 4. 配置资源限制

```yaml
backend:
  resources:
    limits:
      cpu: 2000m
      memory: 4Gi
    requests:
      cpu: 500m
      memory: 1Gi
```

### 5. 使用私有镜像仓库

```yaml
global:
  imageRegistry: registry.yourcompany.com/clawith

# 如果需要认证，创建 imagePullSecret
# kubectl create secret docker-registry regcred \
#   --docker-server=registry.yourcompany.com \
#   --docker-username=user \
#   --docker-password=password \
#   -n clawith
```

## 💡 实用技巧

### 预览部署内容

在实际部署前，先预览生成的 YAML：

```bash
# 渲染模板但不安装
helm template clawith ./helm/clawith -n clawith > preview.yaml

# 或使用 --dry-run
helm install clawith ./helm/clawith -n clawith --dry-run --debug
```

### 比较配置差异

安装 helm-diff 插件来比较配置变更：

```bash
# 安装插件
helm plugin install https://github.com/databus23/helm-diff

# 查看升级差异
helm diff upgrade clawith ./helm/clawith -n clawith
```

### 导出当前配置

```bash
# 导出当前使用的 values
helm get values clawith -n clawith > current-values.yaml

# 导出完整的 manifest
helm get manifest clawith -n clawith > current-manifest.yaml
```

### 只更新特定组件

```bash
# 只更新后端镜像版本
helm upgrade clawith ./helm/clawith -n clawith \
  --set backend.image.tag=v1.0.1 \
  --reuse-values

# 只更新前端配置
helm upgrade clawith ./helm/clawith -n clawith \
  --set frontend.ingress.host=new.example.com \
  --reuse-values

# 更新镜像仓库地址
helm upgrade clawith ./helm/clawith -n clawith \
  --set global.imageRegistry=new-registry.com/project \
  --reuse-values
```

## 📊 监控和维护

### 查看资源使用

```bash
# 查看 Pod 资源使用
kubectl top pods -n clawith

# 查看 Node 资源使用
kubectl top nodes

# 查看 PVC 使用情况
kubectl get pvc -n clawith
```

### 定期备份

**备份 PostgreSQL 数据：**

```bash
# 导出数据库
kubectl exec -n clawith clawith-postgresql-0 -- \
  pg_dump -U postgres clawith > backup-$(date +%Y%m%d).sql

# 恢复数据库
kubectl exec -i -n clawith clawith-postgresql-0 -- \
  psql -U postgres clawith < backup-20260402.sql
```

**备份 Helm 配置：**

```bash
# 备份当前配置
helm get values clawith -n clawith > backup-values-$(date +%Y%m%d).yaml
```

## 🎯 与原有 K8s 部署对比

| 特性 | 原有 K8s YAML | Helm Chart |
|------|--------------|------------|
| 配置管理 | 分散在多个文件 | 集中在 values.yaml |
| 版本控制 | 手动管理 | Helm 自动追踪 |
| 升级 | 逐个 apply | `helm upgrade` 一条命令 |
| 回滚 | 困难 | `helm rollback` 一条命令 |
| 参数化 | 需要手动替换 | 模板自动渲染 |
| 环境管理 | 复制多份 YAML | 一套模板 + values.yaml |
| 依赖管理 | 手动管理顺序 | Helm 自动处理 |
| 可维护性 | 低 | 高 |

## 📚 更多信息

- **详细配置文档**：`helm/clawith/README.md`
- **Helm 官方文档**：https://helm.sh/docs/
- **Kubernetes 文档**：https://kubernetes.io/docs/

## ❓ 常见问题

**Q: 如何查看当前使用的配置？**
```bash
helm get values clawith -n clawith
```

**Q: 如何只更新某个配置项而不影响其他配置？**
```bash
helm upgrade clawith ./helm/clawith -n clawith --reuse-values --set backend.image.tag=v1.0.1
```

**Q: 卸载后如何保留数据？**
```bash
# Helm 卸载默认不会删除 PVC，数据会保留
helm uninstall clawith -n clawith
# PVC 仍然存在，下次安装时可以继续使用
```

**Q: 如何查看部署失败的原因？**
```bash
kubectl get events -n clawith --sort-by='.lastTimestamp'
kubectl logs -n clawith <pod-name>
helm status clawith -n clawith
```

**Q: 如何更换镜像仓库？**
```bash
# 方式1：修改 values.yaml 中的 global.imageRegistry
# 方式2：使用 --set 参数
helm upgrade clawith ./helm/clawith -n clawith \
  --set global.imageRegistry=new-registry.com/project
```

**Q: 如何验证 StorageClass 是否可用？**
```bash
# 查看可用的 StorageClass
kubectl get storageclass

# 查看 StorageClass 详情
kubectl describe storageclass nfs-client
```

---

**祝你部署顺利！** 🎉
