# Clawith 自定义修改记录

记录 Clawith 升级时需要保留的修改，避免被覆盖。

---

## 1. httpx 禁用系统代理

**文件**: `backend/app/services/llm_client.py`

**位置**: 4 个客户端类的 `_get_client()` 方法

- Line 215: `OpenAICompatibleClient._get_client()`
- Line 546: `GeminiClient._get_client()`
- Line 852: `AnthropicClient._get_client()`
- Line 1343: `OllamaClient._get_client()`

**问题**: httpx 默认读取系统代理设置，导致请求被拦截，LLM 调用返回 502。

**修改**: 在创建 `httpx.AsyncClient` 时添加 `trust_env=False`

```python
async def _get_client(self) -> httpx.AsyncClient:
    if self._client is None or self._client.is_closed:
        self._client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, trust_env=False)
    return self._client
```

> 搜索 `trust_env=False` 确认所有 4 处都已修改

---

## 2. Windows subprocess 支持 (Python 3.12)

**文件**: `backend/app/services/agent_tools.py`

### 2.1 模块级事件循环策略

**位置**: 文件开头，`from loguru import logger` 之后

**问题**: Python 3.12 在 Windows 上默认 `SelectorEventLoop` 不支持 subprocess，导致 `asyncio.create_subprocess_exec` 抛出 `NotImplementedError`。

**修改**:

```python
from loguru import logger

import sys as _sys
if _sys.platform == "win32":
    import asyncio as _asyncio
    _asyncio.set_event_loop_policy(_asyncio.WindowsProactorEventLoopPolicy())
```

### 2.2 subprocess 编码修复

**位置**: `_execute_code` 函数内，`await asyncio.wait_for(proc.communicate(), timeout=timeout)` 之后

**问题**: Windows subprocess 默认输出用 GBK 编码，但代码用 UTF-8 解码导致乱码。

**修改**:

```python
# 修改前
stdout_str = stdout.decode("utf-8", errors="replace")[:10000]
stderr_str = stderr.decode("utf-8", errors="replace")[:5000]

# 修改后
if _sys.platform == "win32":
    def _try_decode_win(data):
        if not data:
            return ""
        if len(data) >= 2 and data[:2] == b"\xff\xfe":
            return data[2:].decode("utf-16-le", errors="replace")
        null_count = sum(1 for i in range(1, min(len(data), 1000), 2) if data[i] == 0)
        if null_count / max(len(data) // 2, 1) > 0.3:
            return data.decode("utf-16-le", errors="replace")
        return data.decode("gbk", errors="replace")
    stdout_str = _try_decode_win(stdout)[:10000]
    stderr_str = _try_decode_win(stderr)[:5000]
else:
    stdout_str = stdout.decode("utf-8", errors="replace")[:10000]
    stderr_str = stderr.decode("utf-8", errors="replace")[:5000]
```

> 2026-03-21 更新：加了 UTF-16LE 检测，解决 PowerShell 输出的 UTF-16LE 编码问题。

### 2.3 bash 命令改用 PowerShell

**位置**: `_execute_code` 函数内，`if language == "bash"` 分支

**问题**: Windows 没有 `bash` 命令。Git Bash 会检测 WSL 环境，但 WSL 没有安装 Linux 分发版时会导致 `agent-browser` 等工具出错。

**修改**:

```python
# 修改前
elif language == "bash":
    ext = ".sh"
    import shutil as _shutil
    if _shutil.which("bash"):
        cmd_prefix = ["bash"]
    elif _shutil.which("cmd"):
        cmd_prefix = ["cmd", "/c"]
    else:
        cmd_prefix = ["powershell", "-Command"]

# 修改后
elif language == "bash":
    ext = ".bat"
    cmd_prefix = ["powershell", "-Command"]
```

> 2026-03-21 更新：删除了 bash/cmd 自动检测，直接固定用 PowerShell，避免 Git Bash 检测 WSL 导致的问题。

---

## 3. feishu tool_call 历史记录修复

**文件**: `backend/app/api/feishu.py`

**位置**: `_call_agent_llm` 函数内，历史记录构建部分（约 line 992）

**问题**: feishu 路由的消息在加载历史时跳过了 `role='tool_call'` 的消息，导致多轮工具调用对话中工具调用信息丢失，LLM 报错 "No tool output found for function call"。

**修改**: 将简单的列表推导式替换为循环，正确处理 `tool_call` 角色：

```python
# 修改前
_history = [{"role": m.role, "content": m.content} for m in reversed(_hist_r.scalars().all())]

# 修改后
_hist_list = list(reversed(_hist_r.scalars().all()))
_history = []
for m in _hist_list:
    if m.role == "tool_call":
        import json as _j_tc
        try:
            tc_data = _j_tc.loads(m.content)
            tc_name = tc_data.get("name", "unknown")
            tc_args = tc_data.get("args", {})
            tc_result = tc_data.get("result", "")
            tc_id = f"call_{m.id}"
            _history.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tc_id,
                    "type": "function",
                    "function": {"name": tc_name, "arguments": _j_tc.dumps(tc_args, ensure_ascii=False)},
                }],
            })
            _history.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": str(tc_result)[:500],
            })
        except Exception:
            continue
    else:
        entry = {"role": m.role, "content": m.content}
        if hasattr(m, 'thinking') and m.thinking:
            entry["thinking"] = m.thinking
        _history.append(entry)
```

---

## 4. emoji 日志编码修复

**文件**: `backend/app/main.py`

**位置**: `migrate_enterprise_info()` 函数内的 print 语句

**问题**: Windows GBK 终端无法打印 emoji 字符，导致启动时异常退出。

**修改**: 将 emoji 替换为 ASCII 字符：

```python
# 修改前
print(f"[startup] ✅ Migrated enterprise_info → enterprise_info_{_tenant.id}", flush=True)
print(f"[startup] ℹ️ enterprise_info_{_tenant.id} already exists, skipping migration", flush=True)

# 修改后
print(f"[startup] [OK] Migrated enterprise_info -> enterprise_info_{_tenant.id}", flush=True)
print(f"[startup] [i] enterprise_info_{_tenant.id} already exists, skipping migration", flush=True)
```

---

## 5. 启动脚本路径修复

**文件**: `links.bat`、`clawith.bat`

**问题**: Windows 上 venv Scripts 路径为 `.venv\Scripts\` 而非 `.venv/bin/`。

**修改** (`links.bat`):
```bash
# 修改前
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port $BACKEND_PORT

# 修改后
.venv/Scripts/uvicorn app.main:app --host 0.0.0.0 --port $BACKEND_PORT
```

**修改** (`clawith.bat`):
```bat
REM 修改前
uv run uvicorn app.main:app --reload --port 8008

REM 修改后
.venv\Scripts\uvicorn app.main:app --port 8008
```

> 注意：移除了 `--reload` 参数，因为 watchfiles 子进程可能与 Windows 事件循环冲突。

---

## 6. 禁用 Agent Seeder

**文件**: `backend/app/main.py`

**位置**: `startup()` 函数（约 line 181-184）

**问题**: 每次重启服务都会运行 `seed_default_agents()`，覆盖用户自定义的 Agent。

**修改**: 注释掉相关调用：

```python
# await seed_default_agents()
# 如果以上命令报错，可能是重复的 agent name，执行以下命令解决：
# truncate_table("agents")
```

---

## 7. MCP 工具在 Agent Tools 分配页面不显示

**文件**: `backend/app/api/tools.py`

**位置**: `GET /api/tools/agents/{agent_id}/with-config` 端点（约 line 188 和 line 400）

**问题**: MCP 工具只有在 Agent 已有分配记录时才显示，用户从未分配过所以看不到，形成"先有鸡还是先有蛋"的问题。

**修改**: 删除两处 `if t.type == "mcp" and not at: continue` 检查

```python
# 修改前
tid = str(t.id)
at = assignments.get(tid)
# MCP tools only show for agents that have an explicit assignment
if t.type == "mcp" and not at:
    continue
enabled = at.enabled if at else t.is_default

# 修改后
tid = str(t.id)
at = assignments.get(tid)
enabled = at.enabled if at else t.is_default
```

两处均修改（line 188 和 line 400）。

两处均修改（line 188 和 line 400）。

## 8. 前端聊天输入框卡顿优化

**文件**: `frontend/src/pages/AgentDetail.tsx`

**问题**: 输入框打字延迟——每次按键触发 `setChatInput` 更新 state，导致整个父组件（4400+行）re-render。

**修改**:

1. 删除全部 `refetchInterval`（4处）：避免不必要的定时数据刷新触发 re-render。

2. 输入框改为 uncontrolled 模式，完全绕过 React state：
   - `ChatInput` 组件去掉 `value/onChange`，使用原生 `<input>`
   - `sendChatMsg` 直接从 `chatInputRef.current.value` 读取输入值
   - 发送后直接清空 DOM：`if (chatInputRef.current) chatInputRef.current.value = ''`
   - `sendChatMsg` 加上 `useCallback`

```tsx
// ChatInput 组件改为 uncontrolled
const ChatInput = React.memo(({ onKeyDown, onPaste, placeholder, disabled, autoFocus, inputRef }) => (
    <input ref={inputRef} className="chat-input" onKeyDown={onKeyDown} onPaste={onPaste}
        placeholder={placeholder} disabled={disabled} style={{ flex: 1 }} autoFocus={autoFocus} />
));

// sendChatMsg 读 DOM 而非 state
const _inputEl = chatInputRef.current;
if (!_inputEl) return;
const _inputVal = _inputEl.value.trim();
if (!_inputVal && attachedFiles.length === 0) return;
const userMsg = _inputVal;

// 发送后清空 DOM
if (chatInputRef.current) chatInputRef.current.value = '';

// 发送按钮禁用条件修复
<button onClick={sendChatMsg} disabled={!wsConnected}>Send</button>
```

## 9. 其他（已在 v1.7.1 合并）

以下修复在 v1.7.1 中已合并，无需手动修改：

- `write_text()` 添加 `encoding="utf-8"` 解决 Windows GBK 编码问题
- Skill 创建时初始化 `files = []` 避免异步懒加载错误
