# Clawith 正式环境发布步骤

## 环境信息

| 项目 | 值 |
|------|------|
| **正式服务器** | 39.97.36.137（阿里云） |
| **部署目录** | /opt/clawith/ |
| **Docker Compose** | /opt/clawith/docker-compose.yml |
| **镜像仓库** | yeyecha-registry.cn-hangzhou.cr.aliyuncs.com |
| **数据库** | PostgreSQL 15，用户 clawith，密码 clawith123 |
| **当前 alembic 版本** | add_llm_max_output_tokens |

## 现有数据量

| 数据 | 大小/数量 |
|------|-----------|
| 用户 | 35 |
| Agent | 7 |
| 对话消息 | 323 |
| Agent 文件（volume） | 113.5MB |
| 数据库（volume） | 69.2MB |

---

## 发布步骤

### 阶段 1：备份（在正式服务器 39.97.36.137 上执行）

#### 1.1 备份数据库
```bash
# 创建备份目录
mkdir -p /opt/clawith/backups/$(date +%Y%m%d)

# 导出完整数据库
docker exec clawith-postgres-1 pg_dump -U clawith -d clawith -F custom \
  -f /tmp/clawith_backup.dump

docker cp clawith-postgres-1:/tmp/clawith_backup.dump \
  /opt/clawith/backups/$(date +%Y%m%d)/clawith_db.dump

# 验证备份文件
ls -lh /opt/clawith/backups/$(date +%Y%m%d)/clawith_db.dump
```

#### 1.2 备份 Agent 文件数据
```bash
# 导出 agent_data volume
docker run --rm -v clawith_agent_data:/data -v /opt/clawith/backups/$(date +%Y%m%d):/backup \
  alpine tar czf /backup/agent_data.tar.gz -C /data .

# 验证
ls -lh /opt/clawith/backups/$(date +%Y%m%d)/agent_data.tar.gz
```

#### 1.3 备份 Redis 数据
```bash
docker run --rm -v clawith_redisdata:/data -v /opt/clawith/backups/$(date +%Y%m%d):/backup \
  alpine tar czf /backup/redis_data.tar.gz -C /data .
```

#### 1.4 备份当前 docker-compose.yml
```bash
cp /opt/clawith/docker-compose.yml /opt/clawith/backups/$(date +%Y%m%d)/docker-compose.yml.bak
cp /opt/clawith/.env /opt/clawith/backups/$(date +%Y%m%d)/.env.bak 2>/dev/null
```

#### 1.5 验证备份完整性
```bash
ls -lh /opt/clawith/backups/$(date +%Y%m%d)/
# 应该看到：
# - clawith_db.dump（数据库，~5-10MB）
# - agent_data.tar.gz（Agent 文件，~50MB+）
# - redis_data.tar.gz（Redis）
# - docker-compose.yml.bak
# - .env.bak
```

**⚠️ 确认备份文件大小合理后，才能进行下一步。**

---

### 阶段 2：构建镜像（在新 Mac 10.0.100.1 上执行）

#### 2.1 确认代码是最新
```bash
cd ~/Projects/clawith-fork
git log --oneline -5
# 确认最新 commit 是 b022ede
```

#### 2.2 构建生产镜像
```bash
# 后端
DOCKER_BUILDKIT=0 docker build \
  --build-arg CLAWITH_PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
  --build-arg CLAWITH_PIP_TRUSTED_HOST=mirrors.aliyun.com \
  -t yeyecha-registry.cn-hangzhou.cr.aliyuncs.com/public/clawith-backend:latest \
  ./backend

# 前端
DOCKER_BUILDKIT=0 docker build \
  -t yeyecha-registry.cn-hangzhou.cr.aliyuncs.com/public/clawith-frontend:latest \
  ./frontend
```

#### 2.3 推送到镜像仓库
```bash
docker push yeyecha-registry.cn-hangzhou.cr.aliyuncs.com/public/clawith-backend:latest
docker push yeyecha-registry.cn-hangzhou.cr.aliyuncs.com/public/clawith-frontend:latest
```

---

### 阶段 3：部署（在正式服务器 39.97.36.137 上执行）

#### 3.1 拉取新镜像
```bash
docker pull yeyecha-registry.cn-hangzhou.cr.aliyuncs.com/public/clawith-backend:latest
docker pull yeyecha-registry.cn-hangzhou.cr.aliyuncs.com/public/clawith-frontend:latest
```

#### 3.2 停止并更新服务
```bash
cd /opt/clawith

# 只重建 backend 和 frontend（数据库和 Redis 不动！）
docker compose up -d --no-deps backend frontend
```

#### 3.3 等待健康检查通过
```bash
# 等 30 秒
sleep 30

# 验证
docker ps --format '{{.Names}} {{.Status}}' | grep clawith
curl -s http://localhost:8001/api/health
# 应返回 {"status":"ok","version":"1.7.2"}
```

#### 3.4 验证数据库迁移
```bash
# 查看 alembic 是否执行了新迁移
docker exec clawith-postgres-1 psql -U clawith -d clawith \
  -c 'SELECT * FROM alembic_version;'

# 查看后端启动日志是否有迁移错误
docker logs clawith-backend-1 --tail 30 2>&1 | grep -i 'alembic\|error\|migration'
```

#### 3.5 验证数据完整性
```bash
# 用户数量不变
docker exec clawith-postgres-1 psql -U clawith -d clawith \
  -c 'SELECT count(*) FROM users;'
# 应为 35

# Agent 数量不变
docker exec clawith-postgres-1 psql -U clawith -d clawith \
  -c 'SELECT count(*) FROM agents;'
# 应为 7

# 对话消息不减少
docker exec clawith-postgres-1 psql -U clawith -d clawith \
  -c 'SELECT count(*) FROM chat_messages;'
# 应 >= 323

# Agent 文件完整
docker run --rm -v clawith_agent_data:/data alpine \
  find /data -maxdepth 1 -type d | wc -l
# 应 >= 8（7个 agent + 1个 enterprise_info + 根目录）
```

#### 3.6 功能验收
- [ ] 访问前端页面，确认 Lucide 图标正常显示（无 emoji）
- [ ] 用管理员登录，聊天 tab 在第一位
- [ ] 新建 Agent 不会自动出现 Morty/Meeseeks
- [ ] SQL Execute 工具出现在工具列表中（默认关闭）
- [ ] 用非创建者账号访问 Agent，看不到工作日志 tab
- [ ] 用创建者账号查看 Mind tab，Secrets 区块可见

---

### 阶段 4：回滚方案（如果出现问题）

#### 4.1 回滚镜像
```bash
cd /opt/clawith

# 恢复旧镜像 tag
docker tag 972e6d8b5ca0 yeyecha-registry.cn-hangzhou.cr.aliyuncs.com/public/clawith-backend:latest
docker tag fc9960ed613f yeyecha-registry.cn-hangzhou.cr.aliyuncs.com/public/clawith-frontend:latest

# 重启
docker compose up -d --no-deps backend frontend
```

#### 4.2 回滚数据库（极端情况）
```bash
# 停止后端
docker compose stop backend

# 恢复数据库
docker exec -i clawith-postgres-1 pg_restore -U clawith -d clawith --clean --if-exists \
  < /opt/clawith/backups/$(date +%Y%m%d)/clawith_db.dump

# 恢复 agent 文件
docker run --rm -v clawith_agent_data:/data -v /opt/clawith/backups/$(date +%Y%m%d):/backup \
  alpine sh -c "rm -rf /data/* && tar xzf /backup/agent_data.tar.gz -C /data"

# 重启
docker compose up -d
```

---

## 风险评估

| 风险 | 级别 | 缓解措施 |
|------|------|---------|
| 数据库迁移失败 | 中 | 有完整 pg_dump 备份，可回滚 |
| Agent 文件丢失 | 低 | agent_data volume 不会被 compose up 影响 |
| 新代码 bug | 低 | 已在 dev 环境充分测试，回滚脚本就绪 |
| 镜像拉取失败 | 低 | 先 pull 再 up，确保镜像就位 |
| 旧连接串明文残留 | 无 | 新代码只影响新记录，旧记录不变 |

## 注意事项

1. **PostgreSQL 和 Redis 不重启** — `docker compose up -d --no-deps backend frontend` 只更新应用层
2. **Volume 不会丢失** — compose up 不会删除 named volume
3. **alembic 自动迁移** — 后端 entrypoint.sh 会自动执行 `alembic upgrade head`
4. **PG 密码不同** — 正式环境是 `clawith123`，dev 是 `clawith`，不要搞混
