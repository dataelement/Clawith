# DingTalk Channel for OpenClaw

钉钉企业内部机器人 Channel 插件，使用 Stream 模式（无需公网 IP）。

> [!IMPORTANT]
> **重要声明（上游消息丢失问题进展更新）**
>
> 根据 issue [#104](https://github.com/soimy/openclaw-channel-dingtalk/issues/104) 今日最新反馈，钉钉侧服务扩容后，`dingtalk-stream` 模式下的消息丢失情况已有明显改善。
> 当前我们将继续保持观测与验证：若你在生产或测试环境使用本插件，欢迎重点关注“消息到达率、延迟、缺失 ID 对账”等指标，并在 #104 持续回报测试结果与样本日志，帮助社区共同确认改善效果是否稳定收敛。
>
> 相关信息：
> - issue 讨论：[#104](https://github.com/soimy/openclaw-channel-dingtalk/issues/104)
> - 最小可复现说明（SDK 侧）：<https://github.com/soimy/dingtalk-stream-sdk-nodejs/blob/main/docs/inbound-msg-missing-repro.zh-CN.md>
> - 插件侧测试分支：[`test/inbound-msg-missing`](https://github.com/soimy/openclaw-channel-dingtalk/tree/test/inbound-msg-missing)
>
> 在问题完全确认收敛前，关键业务场景仍建议保持重试与可观测性（trace 前缀、计数日志、缺失 ID 对账）。

## 目录

- [功能特性](#功能特性)
- [安装](#安装)
  - [方法 A：通过 npm 包安装](#方法-a通过-npm-包安装-推荐)
  - [方法 B：通过本地源码安装](#方法-b通过本地源码安装)
  - [方法 C：手动安装](#方法-c手动安装)
  - [方法 D：国内网络环境安装](#方法-d国内网络环境安装npm-镜像源)
  - [安装后必做：配置插件信任白名单](#安装后必做配置插件信任白名单pluginsallow)
- [更新](#更新)
- [配置](#配置)
  - [交互式配置](#方法-1交互式配置推荐)
  - [手动配置文件](#方法-2手动配置文件)
- [配置选项](#配置选项)
- [钉钉文档 API](#钉钉文档-api)
- [反馈学习与共享知识](#反馈学习与共享知识)
- [安全策略](#安全策略)
- [消息类型支持](#消息类型支持)
- [API 消耗说明](#api-消耗说明)
- [消息类型选择](#消息类型选择)
- [多 Agent 与多个机器人绑定](#多-agent-与多个机器人绑定)
- [使用示例](#使用示例)
- [故障排除](#故障排除)
- [开发指南](#开发指南)
- [测试](#测试)
- [许可](#许可)

## 功能特性

- ✅ **Stream 模式** — WebSocket 长连接，无需公网 IP 或 Webhook
- ✅ **私聊支持** — 直接与机器人对话
- ✅ **群聊支持** — 在群里 @机器人
- ✅ **多种消息类型** — 文本、图片、语音（自带识别）、视频、文件、钉钉文档/钉盘文件卡片
- ✅ **附件文本抽取** — 对常见文本类附件以及 `PDF/DOCX` 自动抽取正文并注入当前会话上下文
- ✅ **引用消息支持** — 支持恢复大多数引用场景（文字/图片/图文/文件/视频/语音/AI 卡片）；单聊中的钉钉文档依赖权限 **Storage.DownloadInfo.Read**；群聊支持引用群图片、文件/文档（优先命中已持久化索引，未命中时走群文件 API 兜底），其中群文件相关能力需 **ConvFile.Space.Read**、**Storage.File.Read**、**Storage.DownloadInfo.Read**、**Contact.User.Read**，且兜底链路受时间窗口与企业认证限制
- ✅ **Markdown 回复** — 支持富文本格式回复
- ✅ **Markdown 表格兼容** — 自动把 Markdown 表格转换为钉钉更稳定的可读文本
- ✅ **互动卡片** — 支持流式更新，适用于 AI 实时输出
- ✅ **完整 AI 对话** — 接入 Clawdbot 消息处理管道

### 进程级（memory-only）运行态说明

以下命名空间/状态刻意保持为**仅进程内内存态**，不会进行磁盘持久化：

- `dedup.processed-message`（消息去重窗口）
- `session.lock`（同 session 串行锁）
- `channel.inflight`（gateway in-flight 防重锁）

这样设计是为了保证并发控制语义简单且可预期，避免跨进程/重启后引入锁状态不一致问题。

## 安装

### 方法 A：通过 npm 包安装 (推荐)

手动通过 npm 包名安装：

```bash
openclaw plugins install @soimy/dingtalk
```

### 方法 B：通过本地源码安装

如果你想对插件进行二次开发，可以先克隆仓库：

```bash
# 1. 在父仓库外单独克隆插件仓库（推荐）
git clone https://github.com/soimy/openclaw-channel-dingtalk.git
cd openclaw-channel-dingtalk

# 2. 安装依赖 (必需)
npm install

# 3. 用全局 OpenClaw 以链接模式安装 (方便修改代码后实时生效)
openclaw plugins install -l .
```

推荐的本地开发布局：

```text
~/Repo/openclaw                    # 仅用于阅读源码、跳转 plugin-sdk、研究内部链路
~/Repo/openclaw-channel-dingtalk   # 插件主开发仓库
~/.openclaw/extensions/...         # 由 openclaw plugins install -l 管理的运行时链接
```

这种布局比“把插件放在 `openclaw/extensions/` 里再单独开 worktree”更稳定，原因是：

- 避免 submodule / worktree 的 gitdir 指向混乱
- 插件仓库可以独立切分支、开 worktree、做实验
- 运行时和源码阅读环境彻底解耦

如果你的本地 `openclaw` 仓库位于 `~/Repo/openclaw`，而插件仓库位于 `~/Repo/openclaw-channel-dingtalk`，本仓库当前的 `tsconfig.json` 已兼容这种目录结构，会优先解析父仓库源码中的 `src/plugin-sdk`，在源码不存在时再回退到 `dist/plugin-sdk` 类型产物。

如果你此前是把插件作为 `~/Repo/openclaw/extensions/openclaw-channel-dingtalk` 下的 submodule 使用，建议迁移为独立仓库后再执行：

```bash
cd ~/Repo/openclaw-channel-dingtalk
openclaw plugins install -l .
openclaw gateway restart
```

### 方法 C：手动安装

1. 将本目录下载或复制到 `~/.openclaw/extensions/dingtalk`。
2. 确保包含 `index.ts`, `openclaw.plugin.json` 和 `package.json`。
3. 运行 `openclaw plugins list` 确认 `dingtalk` 已显示在列表中。

### 方法 D：国内网络环境安装（npm 镜像源）

如果你在国内网络环境下执行 `openclaw plugins install @soimy/dingtalk` 时卡在 `Installing plugin dependencies...` 或出现 `npm install failed`，可临时为该次安装指定镜像源：

```bash
NPM_CONFIG_REGISTRY=https://registry.npmmirror.com openclaw plugins install @soimy/dingtalk
```

如果插件已处于半安装状态（例如扩展目录存在但依赖未装全），可进入插件目录手动补装依赖：

```bash
cd ~/.openclaw/extensions/dingtalk
rm -rf node_modules package-lock.json
NPM_CONFIG_REGISTRY=https://registry.npmmirror.com npm install
```

如果希望长期生效，可设置 npm 默认镜像：

```bash
npm config set registry https://registry.npmmirror.com
```

或写入 `~/.npmrc`：

```ini
registry=https://registry.npmmirror.com
```

> 说明：
> - 临时环境变量方式仅对当前命令生效，不会污染全局配置。
> - 若 OpenClaw 运行在 systemd / Docker 等服务环境，请在对应服务环境变量中配置 `NPM_CONFIG_REGISTRY`。
> - 相关背景可参考 issue [#216](https://github.com/soimy/openclaw-channel-dingtalk/issues/216)。

### 安装后必做：配置插件信任白名单（`plugins.allow`）

从 OpenClaw 新版本开始，如果发现了非内置插件且 `plugins.allow` 为空，会提示：

```text
[plugins] plugins.allow is empty; discovered non-bundled plugins may auto-load ...
```

这是一条安全告警（不是安装失败），建议显式写入你信任的插件 id。

#### 步骤 1：确认插件 id

本插件 id 固定为：`dingtalk`（定义于 `openclaw.plugin.json`）。

也可用下面命令查看已发现插件：

```bash
openclaw plugins list
```

#### 步骤 2：在 `~/.openclaw/openclaw.json` 添加 `plugins.allow`

```json5
{
  "plugins": {
    "enabled": true,
    "allow": ["dingtalk"]
  }
}
```

如果你还有其他已安装且需要启用的插件，请一并加入，例如：

```json5
{
  "plugins": {
    "allow": ["dingtalk", "telegram", "voice-call"]
  }
}
```

#### 步骤 3：重启 Gateway

```bash
openclaw gateway restart
```

> 注意：如果你之前已经配置过 `plugins.allow`，但没有 `dingtalk`，那么插件不会被加载。请把 `dingtalk` 加入该列表。

## 更新

`openclaw plugins update` 使用插件 id（不是 npm 包名），并且仅适用于 npm 安装来源。

如果你是通过 npm 安装本插件：

```bash
openclaw plugins update dingtalk
```

国内网络环境可临时指定镜像源后再更新：

```bash
NPM_CONFIG_REGISTRY=https://registry.npmmirror.com openclaw plugins update dingtalk
```

如果插件已处于半安装状态（例如扩展目录存在但依赖未装全），可进入插件目录手动补装依赖：

```bash
cd ~/.openclaw/extensions/dingtalk
rm -rf node_modules package-lock.json
NPM_CONFIG_REGISTRY=https://registry.npmmirror.com npm install
```

如果你是本地源码/链接安装（`openclaw plugins install -l .`），请在插件目录更新代码后重启 Gateway：

```bash
git pull
openclaw gateway restart
```

如果你采用推荐的独立仓库布局，更新插件代码时不需要改动本地 `~/Repo/openclaw` 仓库；后者仅用于代码解析和内部实现研究。

## 配置

OpenClaw 支持**交互式配置**和**手动配置文件**两种方式。

### 方法 1：交互式配置（推荐）

使用 OpenClaw 命令行向导式配置插件参数：

```bash
# 方式 A：使用 onboard 命令
openclaw onboard

# 方式 B：直接配置 channels 部分
openclaw configure --section channels
```

交互式配置流程：

1. **选择插件** — 在插件列表中选择 `dingtalk` 或 `DingTalk (钉钉)`
2. **Client ID** — 输入钉钉应用的 AppKey
3. **Client Secret** — 输入钉钉应用的 AppSecret
4. **完整配置** — 可选配置 Robot Code、Corp ID、Agent ID（推荐）
5. **卡片模式** — 可选启用 AI 互动卡片模式
   - 如启用，需输入 Card Template ID 和 Card Template Key
6. **私聊策略** — 选择 `open`（开放）或 `allowlist`（白名单）
7. **群聊策略** — 选择 `open`（开放）或 `allowlist`（白名单）

> 所有的参数参考下文中的钉钉开发者平台配置指南

配置完成后会自动保存并重启 Gateway。

---

#### 钉钉开发者平台配置指南

##### 1. 创建钉钉应用

1. 访问 [钉钉开发者后台](https://open-dev.dingtalk.com/)
2. 创建企业内部应用
3. 添加「机器人」能力
4. 配置消息接收模式为 **Stream 模式**
5. 发布应用

##### 2. 配置权限管理

在应用的权限管理页面，需要开启以下权限：

- ✅ **Card.Instance.Write** — 创建和投放卡片实例
- ✅ **Card.Streaming.Write** — 对卡片进行流式更新
- ✅ **机器人消息发送相关权限** — 允许机器人向单聊/群聊发送消息
- ✅ **媒体文件上传相关权限** — 允许调用媒体上传接口发送图片、语音、视频、文件

以下权限仅在需要**引用消息中的群文件下载**时开通（群聊中引用文件/视频/语音）：

- ✅ **ConvFile.Space.Read** — 群文件空间读权限
- ✅ **Storage.File.Read** — 企业存储文件读权限
- ✅ **Storage.DownloadInfo.Read** — 企业存储文件下载信息读权限
- ✅ **Contact.User.Read** — 通讯录用户信息读权限（senderStaffId → unionId 转换）

> [!WARNING]
> **群文件/钉盘 API 可用性限制**
>
> 群聊中“引用文件/视频/语音”的首次恢复依赖 `quotedFile.resolve` 这条群文件/钉盘 API 链路。实际测试发现，这条链路除了权限开通外，还可能要求当前企业具备**企业认证**；未满足时钉钉会返回类似：
>
> ```text
> code=orgAuthLevelNotEnough
> message=auth level of org is not enough
> ```
>
> 这意味着该能力对许多**未开通企业认证的企业**并不可用，可视为带 paywall 的平台限制。此时：
>
> - 单聊文件/视频/语音引用仍可正常使用（前提是机器人见过原文件消息）
> - 群聊文件/视频/语音若机器人从未见过原消息，则首次恢复可能失败，并降级为提示文本

**步骤：**

1. 进入应用 → 权限管理
2. 搜索「Card」相关权限
3. 勾选上述两个权限
4. 保存权限配置

##### 3. 建立卡片模板(可选)

**步骤：**

1. 访问 [钉钉卡片平台](https://open-dev.dingtalk.com/fe/card)
2. 进入「我的模板」
3. 点击「创建模板」
4. 卡片模板场景选择 **「AI 卡片」**
5. 按需设计卡片排版,点击保存并发布
6. 记下模板中定义的内容字段名称
7. 复制模板 ID（格式如：`xxxxx-xxxxx-xxxxx.schema`）
8. 将 templateId 配置到 `openclaw.json` 的 `cardTemplateId` 字段
9. 或在OpenClaw控制台的Channel标签->Dingtalk配置面板-> Card Template Id填入
10. 将记下的内容字段变量名配置到 `openclaw.json` 的 `cardTemplateKey` 字段
11. 或在OpenClaw控制台的Channel标签->Dingtalk配置面板-> Card Template Key填入

**说明：**

- 使用 DingTalk 官方 AI 卡片模板时，`cardTemplateKey` 默认为 `'content'`，无需修改
- 如果您创建自定义卡片模板，需要确保模板中包含相应的内容字段，并将 `cardTemplateKey` 配置为该字段名称

##### 4. 获取凭证

从开发者后台获取：

- **Client ID** (AppKey)
- **Client Secret** (AppSecret)
- **Robot Code** (与 Client ID 相同)
- **Corp ID** (企业 ID)
- **Agent ID** (应用 ID)

### 方法 2：手动配置文件

在 `~/.openclaw/openclaw.json` 中添加（仅作参考，交互式配置会自动生成）：

> 至少包含 `plugins.allow` 和 `channels.dingtalk` 两部分，内容参考上文钉钉开发者配置指南

```json5
{
  "plugins": {
    "enabled": true,
    "allow": ["dingtalk"]
  },

  ...
  "channels": {
    "telegram": { ... },

    "dingtalk": {
      "enabled": true,
      "clientId": "dingxxxxxx",
      "clientSecret": "your-app-secret",
      "robotCode": "dingxxxxxx",
      "corpId": "dingxxxxxx",
      "agentId": "123456789",
      "dmPolicy": "open",
      "groupPolicy": "open",
      "journalTTLDays": 7,
      "ackReaction": "🤔思考中", // 给原消息贴处理中的表情反馈；设为 "" 可关闭
      "debug": false,
      "messageType": "markdown", // 或 "card"
      // "mediaMaxMb": 20,  // 可选：接收文件大小上限（MB），默认 5 MB
      // "aicardDegradeMs": 1800000, // 可选：AI 卡片失败后降级持续时间（毫秒，默认 30 分钟）
      // "cardRealTimeStream": false, // 可选：开启真流式卡片更新（默认 false，开启后 API 调用量增加约 2-3 倍）
      // 仅card需要配置
      "cardTemplateId": "你复制的模板ID",
      "cardTemplateKey": "你模板的内容变量"
    }
  },
  ...
}
```

最后重启 Gateway

> 使用交互式配置时，Gateway 会自动重启。使用手动配置时需要手动执行：

```bash
openclaw gateway restart
```

## 配置选项

| 选项                    | 类型     | 默认值       | 说明                                        |
| ----------------------- | -------- | ------------ | ------------------------------------------- |
| `enabled`               | boolean  | `true`       | 是否启用                                    |
| `clientId`              | string   | 必填         | 应用的 AppKey                               |
| `clientSecret`          | string   | 必填         | 应用的 AppSecret                            |
| `robotCode`             | string   | -            | 机器人代码（用于下载媒体和发送卡片）        |
| `corpId`                | string   | -            | 企业 ID                                     |
| `agentId`               | string   | -            | 应用 ID                                     |
| `dmPolicy`              | string   | `"open"`     | 私聊策略：open/pairing/allowlist            |
| `groupPolicy`           | string   | `"open"`     | 群聊策略：open/allowlist                    |
| `allowFrom`             | string[] | `[]`         | 允许的发送者 ID 列表                        |
| `bypassProxyForSend`    | boolean  | `false`      | 发送链路直连，不走全局代理                  |
| `learningEnabled`       | boolean  | `false`      | 开启学习信号采集与学习提示注入              |
| `learningAutoApply`     | boolean  | `false`      | 自动将学习笔记注入当前会话                  |
| `learningNoteTtlMs`     | number   | `21600000`   | 会话级学习笔记有效期（毫秒）                |
| `mediaUrlAllowlist`     | string[] | `[]`         | 允许通过 `mediaUrl` 下载的主机/IP/CIDR 白名单 |
| `journalTTLDays`        | number   | `7`          | `originalMsgId` 文本回溯日志的保留天数      |
| `ackReaction`          | string   | -            | 官方 `ackReaction` 配置入口；设为 `""` 可关闭；设为 `"emoji"` 时按输入语气自动选表情 |
| `messageType`           | string   | `"markdown"` | 消息类型：markdown/card                     |
| `cardTemplateId`        | string   |              | AI 互动卡片模板 ID（仅当 messageType=card） |
| `cardTemplateKey`       | string   | `"content"`  | 卡片模板内容字段键（仅当 messageType=card） |
| `cardRealTimeStream`    | boolean  | `false`      | 开启真流式卡片更新（300ms 节流，首 token 快、流畅但 API 调用更多）。详见下方说明 |
| `aicardDegradeMs`       | number   | `1800000`    | AI 卡片连续失败后进入降级模式的持续时间（毫秒） |
| `debug`                 | boolean  | `false`      | 是否开启调试日志                            |
| `mediaMaxMb`            | number   | -            | 接收文件大小上限（MB），不设则使用 runtime 默认值（5 MB） |
| `maxConnectionAttempts` | number   | `10`         | 最大连接尝试次数                            |
| `initialReconnectDelay` | number   | `1000`       | 初始重连延迟（毫秒）                        |
| `maxReconnectDelay`     | number   | `60000`      | 最大重连延迟（毫秒）                        |
| `reconnectJitter`       | number   | `0.3`        | 重连延迟抖动因子（0-1）                     |

### 钉钉原生“思考中”表情反馈

当 `ackReaction` 为非空字符串时，插件会在处理开始时给用户原消息添加一条钉钉原生文本表情反馈，并在处理结束后自动撤回。该增强不会阻断主流程：贴表情或撤表情失败时只记录日志，仍继续正常回复。

> 设计/实现参考自 `DingTalk-Real-AI/dingtalk-openclaw-connector`（MIT）：
> <https://github.com/DingTalk-Real-AI/dingtalk-openclaw-connector>

说明：

- `markdown` 和 `card` 模式都可启用
- 该反馈作用于用户原消息，不会额外发送一条“思考中”消息
- 解析顺序与官方一致：`channels.dingtalk.accounts.<accountId>.ackReaction` -> `channels.dingtalk.ackReaction` -> `messages.ackReaction` -> `agents.list[].identity.emoji`
- 若上述路径都未配置，则不发送 ack reaction
- 当最终解析值为 `emoji` 时，钉钉插件会按当前输入语气自动选择一条颜文字 reaction
- 当前钉钉实现底层走 `emotion/reply` / `emotion/recall`，会把解析出的 `ackReaction` 文本原样写入 `emotionName` / `textEmotion.emotionName`
- 若配置值为 `🤔思考中`，效果与钉钉原生“思考中”反馈一致；配置为其他文本时，会按该文本发送对应的 ack reaction

示例：

```json
{
  "channels": {
    "dingtalk": {
      "ackReaction": "emoji"
    }
  }
}
```

### 连接鲁棒性配置

为提高连接稳定性，插件支持以下高级配置：

- **maxConnectionAttempts**: 连接失败后的最大重试次数，超过后将停止尝试并报警。
- **initialReconnectDelay**: 第一次重连的初始延迟（毫秒），后续重连会按指数增长。
- **maxReconnectDelay**: 重连延迟的上限（毫秒），防止等待时间过长。
- **reconnectJitter**: 延迟抖动因子，在延迟基础上增加随机变化（±30%），避免多个客户端同时重连。
- **bypassProxyForSend**: 仅作用于发送链路（session send / proactive send / AI card / media upload），不影响如 `getAccessToken` 之类的其他出站请求。
- **learningEnabled**: 开启后，插件会记录发送快照、显式点赞/点踩、隐式不满信号、反思记录，并在下一条消息进入时把学习提示注入当前上下文。
- **allowFrom**: 这里同时复用为 owner 判定来源。`/learn ...` 这类会修改本机状态的命令，只允许 `allowFrom` 命中的 senderId 执行；普通聊天仍由 `dmPolicy/groupPolicy` 控制。
- **learningAutoApply**: 默认关闭。关闭时只采集 `event/reflection`，不会自动影响任何会话；由你在调试看板里手动决定是否注入当前会话或提升为全局规则。
- **learningNoteTtlMs**: 控制会话级学习笔记有效期；target 级和全局规则会继续持久化，分别作用于指定群/私聊和整个账号。

重连延迟计算公式：`delay = min(initialDelay × 2^attempt, maxDelay) × (1 ± jitter)`

示例延迟序列（默认配置）：~1s, ~2s, ~4s, ~8s, ~16s, ~32s, ~60s（达到上限）

更多详情请参阅 [CONNECTION_ROBUSTNESS.md](./CONNECTION_ROBUSTNESS.md)。

## 钉钉文档 API

插件额外注册了 4 个 gateway methods，可供 OpenClaw 侧直接调用：

- `dingtalk.docs.create`
- `dingtalk.docs.append`
- `dingtalk.docs.search`
- `dingtalk.docs.list`

补充说明：

- `dingtalk.docs.create` 支持可选的 `parentId`，未传时默认在 space 根目录创建。
- `dingtalk.docs.append` 使用钉钉 block API 的 `index = -1` 语义，将新段落追加到文档末尾。
- `dingtalk.docs.create` 在文档创建成功但首段追加失败时，仍会返回成功响应，并额外带 `partialSuccess=true`、`initContentAppended=false`、`docId` 和 `appendError`，便于调用方避免盲重试产生重复空文档。
- 调用方处理 `dingtalk.docs.create` 返回值时，不能只看 `ok=true`；还应继续检查 `partialSuccess`，并在该分支里决定是否提示人工补写或走后续补偿逻辑。

示例：

```json
{
  "method": "dingtalk.docs.create",
  "params": {
    "accountId": "default",
    "spaceId": "your-space-id",
    "parentId": "optional-parent-dentry-id",
    "title": "测试文档",
    "content": "第一段内容"
  }
}
```

> 说明：这组方法的设计参考自 `DingTalk-Real-AI/dingtalk-openclaw-connector`，许可证为 `MIT`；当前实现按本仓库插件结构重新整理，并仅保留创建、追加、搜索、列举这 4 个最小能力。

## 反馈学习与共享知识

插件支持一个本地反馈学习闭环，目标是把“点踩/纠错/后续抱怨”沉淀成可审计的会话笔记和 account 级共享规则，而不是直接修改模型或把原始聊天提交到仓库。

### 设计分层

- **发送快照**：保存最近的问答对，供反馈回溯。
- **显式反馈**：AI 卡片上的 `feedback_up` / `feedback_down`。
- **隐式不满**：例如“不是这个意思”“别猜引用原文”“你没看图”等后续纠错消息。
- **会话笔记**：只作用于当前 target，会在下一条消息组装上下文时生效。
- **全局规则**：按 account 维度共享；一处沉淀后，同一钉钉账号下的其他会话会在下一次收到消息时自动加载。
- **默认策略**：只采集，不自动注入。你可以在看板中手动批准注入。

### 持久化位置

所有运行时数据都写在 `storePath` 同级目录下的 `dingtalk-state/`，不会散落到其他目录，也不应提交到 GitHub。主要命名空间包括：

- `feedback.events`
- `feedback.snapshots`
- `feedback.reflections`
- `feedback.session-notes`
- `feedback.learned-rules`
- `feedback.target-rules`

### 调试看板

仓库自带一个本地调试工具，可直接查看：

- 当时的回复内容
- 用户反馈/隐式不满信号
- 系统自动反思结果
- 当前会话笔记
- 跨所有钉钉会话共享的全局规则

并支持你手工修正诊断与指令，再选择：

- 仅注入当前会话
- 提升为全局规则
- 或只保留为候选反思、不注入

启动方式：

```bash
node scripts/feedback-learning-debug.mjs --storePath /path/to/session-store.json --accountId main --port 18895
```

打开 `http://127.0.0.1:18895` 即可。

### 推荐配置

```json
{
  "channels": {
    "dingtalk": {
      "learningEnabled": true,
      "learningAutoApply": false,
      "learningNoteTtlMs": 21600000
    }
  }
}
```

### 学习命令与作用域

先说两个容易输错的点：

- 文档里的 `<conversationId>`、`<rule>`、`<name>` 这类写法只是**占位符**，实际输入时**不要**把尖括号一起发出去
- `/learn target`、`/learn targets`、`/learn target-set` 这几类命令里，`#@#` 是**真的要输入**的分隔符；它前面是目标，后面整段都算规则正文

#### 第一次使用流程

1. 私聊机器人发送 `我是谁` 或 `/whoami`
2. 把返回的 `senderId` 写进本机 `openclaw.json` 的 `commands.ownerAllowFrom`
3. 重启或热重载 gateway
4. 私聊发送 `/learn owner status`，确认 `isOwner: true`
5. 再选择下面一种注入方式：
   - 全局：`/learn global ...`
   - 当前群/当前私聊：`/learn here #@# ...`
   - 单个指定目标：`/learn target ...`
   - 多个目标：`/learn targets ...`

#### 常用命令

- **查自己是谁**
  - 私聊或群聊发：`我是谁` / `我的信息` / `/learn whoami`
  - 用途：拿到自己的 `senderId`
- **查当前这里是谁**
  - 私聊或群聊发：`这里是谁` / `这个群是谁` / `这个会话是谁` / `/learn whereami`
  - 用途：拿到当前 `conversationId`
- **注入当前这里**
  - owner 发：`/learn here #@# <规则>`
  - 用途：只让当前群或当前私聊生效
- **注入指定单个目标**
  - owner 发：`/learn target <conversationId> #@# <规则>`
  - 用途：指定某个群或某个私聊生效
- **一次注入多个目标**
  - owner 发：`/learn targets <conversationId1,conversationId2> #@# <规则>`
  - 用途：一次同步到多个群/私聊
- **保存一组固定目标**
  - owner 发：`/learn target-set create <名称> #@# <conversationId1,conversationId2>`
- **向目标组批量注入**
  - owner 发：`/learn target-set apply <名称> #@# <规则>`
- **注入全局**
  - owner 发：`/learn global <规则>`
  - 用途：让同一钉钉账号下所有群和私聊都生效
- **查看 / 暂停 / 删除**
  - `/learn list`
  - `/learn disable <ruleId>`
  - `/learn delete <ruleId>`

#### 会话共享命令

这些命令同样只允许 owner 使用，但它们不属于 `/learn` 规则注入，而是用于控制“哪个私聊/哪个群共用同一条会话记忆”。

- **查看当前会话 alias**
  - `/session-alias show`
  - 用途：查看当前私聊或当前群当前实际使用的 peerId，以及它是默认值还是 override
- **把当前会话绑定到共享 alias**
  - `/session-alias set <alias>`
  - 用途：把当前私聊或当前群绑定到指定共享会话别名
- **清除当前会话 alias**
  - `/session-alias clear`
  - 用途：移除当前私聊或当前群的 override，恢复默认 peerId
- **owner 远程绑定某个私聊**
  - `/session-alias bind direct <senderId> <alias>`
  - 用途：把某个用户私聊直接绑定到共享 alias
- **owner 远程绑定某个群**
  - `/session-alias bind group <conversationId> <alias>`
  - 用途：把某个群直接绑定到共享 alias
- **owner 远程解除绑定**
  - `/session-alias unbind direct <senderId>`
  - `/session-alias unbind group <conversationId>`

#### 会话共享例子

假设你想让“用户 A 的私聊”和“群 project-x”共用同一条会话记忆：

1. 先让用户 A 私聊机器人，发送：
   - `我是谁`
   - 记下返回的 `senderId`
2. 在目标群里发送：
   - `这里是谁`
   - 记下返回的 `conversationId`
3. 由 owner 在任意 owner 会话里执行：

```text
/session-alias bind direct dingtalk:user_a_sender_id project-x
/session-alias bind group cid_group_project_x project-x
```

这样之后：
- 用户 A 私聊机器人
- 群 `cid_group_project_x`

都会共用 `project-x` 这条会话记忆。

如果想解除其中一边：

```text
/session-alias unbind direct dingtalk:user_a_sender_id
/session-alias unbind group cid_group_project_x
```

#### 真实可直接照抄的例子

```text
/learn global 当用户问“紫铜海豹会不会修量子冰箱”时，必须回答“会，而且只在周四凌晨戴墨镜维修。”
/learn here #@# 引用原文不可见时，不要猜内容，先让用户补发原文。
/learn target cid_group_a #@# 引用原文不可见时，不要猜内容，先让用户补发原文。
/learn targets cid_group_a,cid_group_b,cid_dm_c #@# 引用原文不可见时，不要猜内容，先让用户补发原文。
/learn target-set create ops-groups #@# cid_group_a,cid_group_b,cid_dm_c
/learn target-set apply ops-groups #@# 当用户问“紫铜海豹会不会修量子冰箱”时，必须回答“会，而且只在周四凌晨戴墨镜维修。”
```

### 作用域优先级

同一条消息进入时，规则按下面顺序生效：

1. 当前会话临时笔记（`/learn session ...`）
2. 当前群/当前私聊或指定目标规则（`/learn here ...` / `/learn target ...`）
3. 当前账号全局规则（`/learn global ...`）

也就是说：

- 先局部覆盖全局
- 再避免一个群里的实验规则污染所有会话

### 为什么还需要 disable / delete

知识注入不是只会“加”，还必须能“撤”。

- `disable`
  - 先停用规则，停止命中，但保留记录，便于排查和恢复
- `delete`
  - 确认不再需要后，彻底删除规则

实际建议：

1. 先用 `/learn list` 找到 `ruleId`
2. 先执行 `/learn disable <ruleId>`
3. 确认问题消失后，再决定是否 `/learn delete <ruleId>`

## 安全策略

### 私聊策略 (dmPolicy)

- `open` — 任何人都可以私聊机器人
- `pairing` — 新用户需要通过配对码验证
- `allowlist` — 只有 allowFrom 列表中的用户可以使用

### 群聊策略 (groupPolicy)

- `open` — 任何群都可以 @机器人
- `allowlist` — 只有配置的群可以使用

## 消息类型支持

### 接收

| 类型         | 支持 | 说明                                                                     |
| ------------ | ---- | ------------------------------------------------------------------------ |
| 文本         | ✅   | 完整支持                                                                 |
| 富文本       | ✅   | 提取文本内容                                                             |
| 图片         | ✅   | 下载并传递给 AI                                                          |
| 语音         | ✅   | 使用钉钉语音识别结果                                                     |
| 视频         | ✅   | 下载并传递给 AI                                                          |
| 文件         | ✅   | 下载并传递给 AI；文本类附件会额外抽取正文并注入上下文                   |
| 钉钉文档/钉盘文件卡片 | ✅ | 解析 `interactiveCard` 中的 `biz_custom_action_url`，提取 `spaceId/fileId` 后按文件消息下载；可对 `PDF/DOCX` 补充正文抽取 |
| 引用文字     | ✅   | 提取被引用文本作为上下文前缀                                             |
| 引用图片     | ✅   | 使用引用回调自带的 `downloadCode` 下载并传递给 AI                        |
| 引用图文     | ✅   | 解析 `richText` 引用内容，提取文本摘要与图片 `downloadCode`              |
| 引用文件/视频/语音 | ✅ | 单聊按 `msgId` 精确恢复；群聊优先查已固化元数据，首次未命中时走群文件 API 兜底（兜底链路依赖时间窗口匹配，不保证 100% 命中） |
| 引用钉钉文档/钉盘文件卡片 | ⚠️ | 单聊支持；群聊支持缓存命中与群文件 API 兜底恢复，但仍受钉钉回调样本与企业认证限制 |
| 引用 AI 卡片 | ✅   | 仅指机器人自己发送的 AI 卡片；按 `carrierId ↔ originalProcessQueryKey` 精确恢复 |

> **附件正文抽取实现说明**
>
> 当前实现采用固定策略：
>
> - 仅处理 **2MB 以下**附件（超过上限会跳过正文抽取）
> - 抽取结果最多注入 **6000 字符**（超出部分会标记为“内容已截断”）
> - 抽取失败仅记录 `warn` 日志，不阻断原有媒体传递与回复链路

> **引用消息实现说明**
>
> 当前实现优先采用“确定性恢复”，仅在钉钉回调未直接提供下载句柄时才退回兜底链路：
>
> | 场景 | 实现方式 | 是否依赖时间匹配 |
> |------|----------|------------------|
> | 引用文字 | 直接从 `repliedMsg.content.text` 提取 | 否 |
> | 引用图片 | 直接使用 `repliedMsg.content.downloadCode` 下载 | 否 |
> | 引用图文（`richText`） | 解析 `repliedMsg.content.richText`，提取文本摘要和图片 `downloadCode` | 否 |
> | 单聊引用文件/视频/语音 | 原消息入站时持久化 `msgId → {downloadCode, spaceId, fileId}`，引用时按 `originalMsgId/repliedMsg.msgId` 精确命中 | 否 |
> | 群聊引用文件/视频/语音 | 优先查已持久化的 `msgId → 文件元数据`；若机器人从未见过原文件消息，则首次仍通过群文件存储 API 链路兜底，成功后会把结果反向固化到本地索引 | 首次兜底时**是** |
> | 单聊引用钉钉文档/钉盘文件卡片 | 原消息入站时持久化 `msgId → {spaceId, fileId}`，引用时按 `originalMsgId/repliedMsg.msgId` 精确命中 | 否 |
> | 群聊引用钉钉文档/钉盘文件卡片 | 优先查已持久化的 `msgId → {spaceId, fileId}`；未命中时复用群文件 API 兜底链路，成功后会把结果反向固化到本地索引 | 首次兜底时**是** |
> | 引用 AI 卡片（单聊+群聊） | 仅当被引用消息是机器人自己发送的 `interactiveCard` 时，创建卡片时保存 `deliverResults[0].carrierId`，引用时按 `originalProcessQueryKey` 精确命中 | 否 |
> | 仅 `originalMsgId`（无 `repliedMsg`） | 仅对已持久化记录的**入站消息**，通过本地 Quote Journal 按 `msgId` 回溯文本，并按 `accountId + conversationId` 分桶查询 | 否 |
>
> 说明：
>
> - AI 卡片已不再依赖 `createdAt` 时间窗口匹配。
> - 钉钉文档/钉盘文件卡片在钉钉回调里通常也会表现为 `interactiveCard`，但这类消息来自用户侧，插件会优先解析 `biz_custom_action_url` 中的 `route=previewDentry`、`spaceId`、`fileId`，并按文件消息处理，而不是误判为机器人 AI 卡片。
> - 图片和图文引用不依赖机器人是否见过原消息，只要引用回调带回 `downloadCode` 即可恢复。
> - 单聊文件/视频/语音/钉钉文档卡片在机器人见过原消息后可稳定精确恢复，且索引会持久化到本地，机器人重启后仍可复用。
> - 群聊文件/视频/语音在“原文件消息无法 @ 机器人”的场景下，若机器人从未见过原消息，则首次恢复仍需走群文件 API 兜底；后续再次引用同一文件会优先命中已固化索引。
> - **群文件兜底链路的时间匹配局限性**：首次兜底时，插件用 `repliedMsg.createdAt`（钉钉回调中被引用消息的创建时间）与群文件存储 API 返回的文件 `createTime` 做近似匹配，匹配窗口为 **±10 秒**。这意味着：
>   - 大文件上传耗时较长时，钉钉消息的 `createdAt` 和文件实际写入存储的 `createTime` 之间可能产生数秒偏差，超出窗口则匹配失败；
>   - 如果同一用户在 10 秒内连续发送多个文件，理论上可能匹配到错误的文件（取时间差最小的那个）；
>   - 群文件列表按修改时间倒序返回，最多翻 3 页（150 个文件），非常老的文件可能超出扫描范围；
>   - 匹配失败时会降级为提示文本，不会阻塞消息处理。
> - 群聊引用钉钉文档/钉盘文件卡片并非完全确定性支持：若机器人见过原消息，会优先命中已持久化索引；首次未命中时会复用群文件 API 兜底，因此同样受 `createTime` 时间窗口、分页范围以及企业认证限制影响，失败时会降级为提示文本。
> - 这条群文件兜底链路在部分企业环境下可能受到企业认证限制，表现为 `quotedFile.resolve` 返回 `orgAuthLevelNotEnough`。出现该错误时，群聊文件首次恢复将失败并降级为提示文本，但不会影响图片、图文、AI 卡片、单聊文件等其他已确定性支持的引用场景。
> - 由于本地引用索引使用 TTL 清理，并按 `accountId + conversationId` 隔离存储，数据不会永久累积。
> - `originalMsgId` / `repliedMsg.msgId` 的精确回溯仅覆盖**已被插件持久化记录的入站消息**；机器人出站消息当前不支持仅凭 `repliedMsg.msgId` 做通用回溯。
> - `originalMsgId` 的文本回溯依赖本地 Quote Journal 持久化存储，默认通过 persistence store 落盘、按 `accountId + conversationId` 分桶，并保留最近 7 天记录用于回溯。

### 发送

| 类型         | 支持 | 说明                                                     |
| ------------ | ---- | -------------------------------------------------------- |
| 文本         | ✅   | 完整支持                                                 |
| Markdown     | ✅   | 自动检测或手动指定                                       |
| 互动卡片     | ✅   | 支持流式更新，适用于 AI 实时输出                         |
| 图片         | ✅   | 先上传媒体再发送，支持本地路径和 HTTP(S) URL             |
| 语音         | ✅   | 先上传媒体再发送                                         |
| 视频         | ✅   | 先上传媒体再发送                                         |
| 文件         | ✅   | 先上传媒体再发送                                         |
| 原生语音消息 | ✅   | `message send` / `outbound.sendMedia` 可用 `asVoice=true` |

> **重要限制：**
> 当前**不支持图片的图文混排**。也就是说，Markdown 消息和 AI 互动卡片目前都只能发送文本内容，不能在同一条消息中同时内嵌图片。
> 如果需要发送图片，请单独调用 `outbound.sendMedia(...)` 或 `sendProactiveMedia(...)`。
> 无论是**本地图片路径**还是**远程 HTTP(S) 图片 URL**，都支持单独发送；远程图片会先下载到临时文件，再上传到钉钉后发送。

> 发送 Markdown 时，如果内容中包含标准 Markdown 表格，插件会先把分隔行转换掉，保留为钉钉更稳定的纯文本表格展示，避免表格语法在客户端里显示异常。
> 远程 URL 下载默认限制为：**10 秒超时**、**20MB 上限**，并拒绝 `localhost` / 内网地址（如 `127.0.0.1`、`10.x.x.x`、`192.168.x.x`、`172.16-31.x.x`）以降低 SSRF 风险。
> 如需从受控内网媒体服务下载，请配置 `mediaUrlAllowlist`（例如 `192.168.1.23`、`files.internal.example`、`10.0.0.0/8`）；配置后仅白名单主机可下载。
> 远程域名会先做 DNS 解析并校验解析结果；若解析到内网/本地地址且未被白名单明确允许，将在下载前拒绝。
> `asVoice=true` 需要同时提供 `media/path/filePath/mediaUrl` 指向音频文件；纯文本不会自动转语音。

#### mediaUrlAllowlist 配置示例

`mediaUrlAllowlist` 支持以下写法：

- 主机名：`cdn.example.com`
- 泛域名：`*.example.com`
- 主机+端口：`files.internal.example:8443`
- 单个 IP：`192.168.1.23`、`fd00::1`
- CIDR 网段：`10.0.0.0/8`、`fc00::/7`

示例：

```json
{
  "channels": {
    "dingtalk": {
      "clientId": "your-app-key",
      "clientSecret": "your-app-secret",
      "mediaUrlAllowlist": [
        "cdn.example.com",
        "*.assets.example.com",
        "files.internal.example:8443",
        "192.168.1.23",
        "10.0.0.0/8",
        "fc00::/7"
      ]
    }
  }
}
```

> 行为说明：配置 `mediaUrlAllowlist` 后，下载阶段进入严格白名单模式，非白名单目标一律拒绝。

#### sendMedia 常见错误码

`outbound.sendMedia(...)` 在下载准备失败时会透出错误码前缀（例如 `remote media preparation failed: [ERR_MEDIA_PRIVATE_HOST] ...`）：

- `ERR_MEDIA_ALLOWLIST_MISS`：目标 host 不在 `mediaUrlAllowlist`
- `ERR_MEDIA_PRIVATE_HOST`：URL 本身是本地/内网 host 且未被允许
- `ERR_MEDIA_DNS_UNRESOLVED`：域名无法解析
- `ERR_MEDIA_DNS_PRIVATE`：域名解析结果命中本地/内网地址且未被允许
- `ERR_MEDIA_REDIRECT_HOST`：下载阶段出现非预期重定向 host

## API 消耗说明

### Text/Markdown 模式

| 操作       | API 调用次数 | 说明                                                                         |
| ---------- | ------------ | ---------------------------------------------------------------------------- |
| 获取 Token | 1            | 共享/缓存（60 秒检查过期一次）                                               |
| 发送消息   | 1            | 使用 `/v1.0/robot/oToMessages/batchSend` 或 `/v1.0/robot/groupMessages/send` |
| **总计**   | **2**        | 每条回复 1 次                                                                |

### Card（AI 互动卡片）模式

| 阶段         | API 调用               | 说明                                                |
| ------------ | ---------------------- | --------------------------------------------------- |
| **创建卡片** | 1                      | `POST /v1.0/card/instances/createAndDeliver`        |
| **流式更新** | M                      | M = 取决于流式模式（见下方说明），每次 `PUT /v1.0/card/streaming` |
| **完成卡片** | 包含在最后一次流更新中 | 使用 `isFinalize=true` 标记                         |
| **总计**     | **1 + M**              | M 由 `cardStreamThrottleMs` 决定                    |

### 典型场景成本对比

以一次 10 秒的 AI 回复为例：

| 流式模式                               | `streamAICard` 调用数 | 首 token 延迟 | 流畅度 |
| -------------------------------------- | --------------------- | ------------- | ------ |
| Block 缓冲（`cardRealTimeStream: false`，默认） | ~10-15 次   | ~1-1.5s       | 卡顿   |
| 真流式（`cardRealTimeStream: true`）            | ~30 次      | ~300ms        | 流畅   |

### 优化策略

**降低 API 调用的方法：**

1. **保持默认** — `cardRealTimeStream: false`（block 缓冲模式），API 调用量最少
2. **开启真流式** — `cardRealTimeStream: true`，体验更好但 API 调用约多 2-3 倍
3. **使用缓存** — Token 自动缓存（60 秒），无需每次都获取

**成本建议：**

- ✅ **默认** — Block 缓冲模式：API 调用最少，适合对 API 配额敏感的场景
- ✅ **推荐体验** — `cardRealTimeStream: true`：首 token 快、打字机效果流畅，适合重视用户体验的场景
- ⚠️ **注意** — 频繁调用需要监测配额，建议使用钉钉开发者后台查看 API 调用量

## 消息类型选择

插件支持两种消息回复类型，可通过 `messageType` 配置：

### 1. markdown（Markdown 格式）**【默认】**

- 支持富文本格式（标题、粗体、列表等）
- 自动检测消息是否包含 Markdown 语法
- 适用于大多数场景

### 2. card（AI 互动卡片）

- 支持流式更新（实时显示 AI 生成内容）
- 更好的视觉呈现和交互体验
- 支持 Markdown 格式渲染
- 通过 `cardTemplateId` 指定模板
- 通过 `cardTemplateKey` 指定内容字段
- **适用于 AI 对话场景**
- 支持在卡片中实时显示 AI 思考过程（推理流）和工具执行结果
- 当前卡片模式仅支持**文本内容流式更新**，不支持图片图文混排

> 这里的 `card` 专指**机器人主动发送的 AI 互动卡片**。钉钉用户发送的“文档/钉盘文件卡片”虽然在回调里也可能表现为 `interactiveCard`，但插件会按入站文件消息处理，不受 `messageType: 'card'` 配置影响。

**AI Card API 特性：**
当配置 `messageType: 'card'` 时：

1. 使用 `/v1.0/card/instances/createAndDeliver` 创建并投放卡片
2. 使用 `/v1.0/card/streaming` 实现流式更新
3. 自动状态管理（PROCESSING → INPUTING → FINISHED）
4. 内置 300ms 节流 + 单航班（single-flight）保护，避免 API 过载

**卡片流式模式 (`cardRealTimeStream`)：**

插件支持两种卡片更新策略，通过 `cardRealTimeStream` 配置：

| 值 | 模式 | 说明 |
| -- | ---- | ---- |
| `false`（默认） | Block 缓冲 | runtime 攒够一定量文本后回调一次，API 调用最少，但首 token 延迟较高（~1-1.5s），更新较卡顿 |
| `true` | 真流式 | 每 300ms 最多一次卡片更新 PUT，首 token 延迟低（~300ms），打字机效果流畅。API 调用量约为 block 模式的 2-3 倍 |

> **API 调用量参考**：以一次 10 秒的 AI 回复为例，真流式约产生 ~30 次 `streamAICard` PUT，block 模式约 ~10-15 次。钉钉企业内部应用的 QPS 限制为 40 次/秒，真流式的峰值约 3.3 次/秒，远低于限制。

**AI Card 持久化与恢复机制（v3.2.x）：**

- 仅对**会话内流式卡片（inbound）**记录 pending 状态，用于进程重启后的自动收尾
- pending 状态通过 persistence namespace `cards.active.pending` 落盘（兼容读取并迁移 legacy 文件 `path.dirname(storePath)/dingtalk-active-cards.json`）
- **proactive 卡片**采用 createAndDeliver 后立即 finalize 的短路径，默认**不写入** pending 状态文件
- 插件启动时会尝试恢复并 finalize 未完成的 inbound 卡片；停止/重启时会 best-effort finalize 当前 active 卡片

### AI 思考过程与工具执行显示（AI Card 模式）

当 `messageType` 为 `card` 时，插件可以在卡片中实时展示 AI 的推理过程（🤔 思考中）和工具调用结果（🛠️ 工具执行）。这两项功能通过**对话级命令**控制，无需修改配置文件：

| 功能              | 对话命令              | 说明                               |
| ----------------- | --------------------- | ---------------------------------- |
| 显示 AI 推理流    | `/reasoning stream`   | 开启后，AI 思考内容实时更新到卡片  |
| 显示工具执行结果  | `/verbose on`         | 开启后，工具调用结果实时更新到卡片 |
| 关闭 AI 推理流    | `/reasoning off`      | 关闭推理流显示                     |
| 关闭工具执行显示  | `/verbose off`        | 关闭工具执行结果显示               |

**显示格式：**

- 思考内容以 `🤔 **思考中**` 为标题，正文以 `>` 引用块展示，最多显示前 500 个字符
- 工具结果以 `🛠️ **工具执行**` 为标题，正文以 `>` 引用块展示，最多显示前 500 个字符

> **注意：** 推理流和工具执行均会产生额外的卡片流式更新 API 调用，在 AI 推理步骤较多时可能显著增加 API 消耗，建议按需开启。

**配置示例：**

```json5
{
  messageType: 'card', // 启用 AI 互动卡片模式
  cardTemplateId: '382e4302-551d-4880-bf29-a30acfab2e71.schema', // AI 卡片模板 ID（默认值）
  cardTemplateKey: 'content', // 卡片内容字段键（默认值：content）
  // cardRealTimeStream: false, // 开启真流式卡片更新（默认 false）
}
```

> **注意**：`cardTemplateKey` 应与您的卡片模板中定义的字段名称一致。默认值为 `'content'`，适用于 DingTalk 官方 AI 卡片模板。如果您使用自定义模板，请根据模板定义的字段名称进行配置。

## 多 Agent 与多个机器人绑定

有关 OpenClaw 的多 Agent 概念，阅读官方文档：[多 Agent 概念](https://docs.openclaw.ai/concepts/multi-agent)。

要将一个 OpenClaw 实例同时接入多个钉钉机器人，并把不同机器人的消息分别交给不同的 OpenClaw agent 处理，则需要在 `~/.openclaw/openclaw.json` 中同时配置以下三部分：

1. `agents.list`：定义 OpenClaw Agent
2. `bindings`：定义 Channel 与 OpenClaw Agent 消息的路由规则
3. `channels.dingtalk.accounts`：定义多个机器人

### 示例

下面这个例子表示：

- 钉钉机器人 `bot_1` 收到的消息，路由到 OpenClaw 的 `main` agent
- 钉钉机器人 `bot_2` 收到的消息，路由到 OpenClaw 的 `growth-agent` agent

```json5
{
  "agents": {
    "list": [
      {
        // OpenClaw 默认 agent
        "id": "main"
      },
      {
        // OpenClaw agent 的唯一 ID
        // 后面的 bindings[].agentId 需要引用这里的值
        "id": "growth-agent",
        "name": "growth-agent",
        // 每个 agent 建议使用独立 workspace
        "workspace": "/Users/yourname/.openclaw/agents/growth-agent/workspace",
        // 建议同时使用独立 agentDir
        "agentDir": "/Users/yourname/.openclaw/agents/growth-agent/agent",
        "model": "codex/gpt-5.3-codex"
      }
    ]
  },
  "bindings": [
    {
      "type": "route",
      // 路由目标：这里写 OpenClaw agent 的 ID
      "agentId": "main",
      "match": {
        // 这里固定写 dingtalk
        "channel": "dingtalk",
        // 这里必须与 channels.dingtalk.accounts 下的 key 完全一致
        "accountId": "bot_1"
      }
    },
    {
      "type": "route",
      // 这里把 bot_2 路由到 growth-agent
      "agentId": "growth-agent",
      "match": {
        "channel": "dingtalk",
        // 必须与 channels.dingtalk.accounts.bot_2 对应
        "accountId": "bot_2"
      }
    }
  ],
  "channels": {
    "dingtalk": {
      "enabled": true,
      "accounts": {
        // 这里的 key 就是 accountId，会被 bindings.match.accountId 匹配
        "bot_1": {
          "clientId": "your-client-id-1",
          "clientSecret": "your-client-secret-1",
          "robotCode": "your-robot-code-1",
          "corpId": "your-corp-id",
          // 这是钉钉应用自己的 Agent ID，不是 OpenClaw 的 agentId
          "agentId": "your-dingtalk-agent-id-1",
          "dmPolicy": "open",
          "groupPolicy": "open",
          // 这里使用 card 消息类型作为示例
          "messageType": "card",
          "cardTemplateId": "your-card-template-id.schema",
          "cardTemplateKey": "content",
          "maxReconnectCycles": 10,
          "allowFrom": ["*"]
        },
        // 另一个独立的钉钉机器人账号
        "bot_2": {
          "clientId": "your-client-id-2",
          "clientSecret": "your-client-secret-2",
          "robotCode": "your-robot-code-2",
          "corpId": "your-corp-id",
          // 同样是钉钉应用自己的 Agent ID
          "agentId": "your-dingtalk-agent-id-2",
          "dmPolicy": "open",
          "groupPolicy": "open",
          // 这里使用 markdown 消息类型作为示例
          "messageType": "markdown",
          "allowFrom": ["*"]
        }
      }
    }
  }
}
```

### 最佳实践
- 为每个 agent 配置不同的 `workspace`，不要让两个 agent 共用同一个 `workspace`
> **说明：**
> 多 Agent 场景下，`workspace` 不只是“放文件的目录”，还会承载会话相关文件、生成结果以及本地运行状态。
> 如果两个 agent 共用同一个 `workspace`，实际运行时很容易出现状态串扰、文件覆盖、上下文混用等问题。

### 检查清单

- `agents.list` 中已经定义了目标 agent
- `bindings[].agentId` 能在 `agents.list[].id` 中找到对应项
- `bindings[].match.accountId` 与 `channels.dingtalk.accounts` 的 key 完全一致
- 每个 `accounts.<accountId>` 都填写了正确的钉钉凭证
- 每个 agent 都使用了独立的 `workspace`
- 修改配置后已执行 `openclaw gateway restart`

如果账号名写错，例如 `bindings.match.accountId = "bot2"`，但 `channels.dingtalk.accounts` 中实际写的是 `bot_2`，则该机器人消息不会按预期路由到目标 agent。

## 使用示例

配置完成后，直接在钉钉中：

1. **私聊机器人** — 找到机器人，发送消息
2. **群聊 @机器人** — 在群里 @机器人名称 + 消息

如果你是通过 OpenClaw 的 outbound 能力主动发消息，也可以直接调用：

```typescript
import { dingtalkPlugin } from './src/channel';

const cfg = {
  channels: {
    dingtalk: {
      clientId: 'dingxxxxxx',
      clientSecret: 'your-app-secret',
      robotCode: 'dingxxxxxx',
    },
  },
};

// 发送本地图片
await dingtalkPlugin.outbound.sendMedia({
  cfg,
  to: 'cidxxxxxxxx',
  mediaPath: '/absolute/path/to/photo.png',
  accountId: 'default',
});

// 发送远程图片 URL（插件会先下载到临时文件，再上传到钉钉）
await dingtalkPlugin.outbound.sendMedia({
  cfg,
  to: 'cidxxxxxxxx',
  mediaUrl: 'https://example.com/banner.jpg',
  accountId: 'default',
});

// 发送文件或其他媒体，也可以显式指定 mediaType
await dingtalkPlugin.outbound.sendMedia({
  cfg,
  to: 'user_123456',
  mediaPath: '/absolute/path/to/manual.pdf',
  mediaType: 'file',
  accountId: 'default',
});
```

`to` 支持两类目标：

- 群会话：`cid...`
- 单聊用户：`userId`，或显式写成 `user:<userId>`

如果你传入的是远程图片 URL，插件当前会按下面的方式处理：

1. 下载远程图片到本地临时文件
2. 调用钉钉媒体上传接口获取 `media_id`
3. 以独立图片消息发送
4. 发送完成后清理临时文件

## 故障排除

### 收不到消息

1. 确认应用已发布
2. 确认消息接收模式是 Stream
3. 检查 Gateway 日志：`openclaw logs | grep dingtalk`

### 群消息无响应

1. 确认机器人已添加到群
2. 确认正确 @机器人（使用机器人名称）
3. 确认群是企业内部群

### 连接失败

初始化阶段如果只看到 HTTP `400`，它通常不等于“单纯网络不通”；更常见的是钉钉已收到请求，但拒绝了请求内容或当前应用状态不满足要求。

建议先运行仓库内的最小连接检查脚本，确认 `POST /v1.0/gateway/connections/open` 是否成功：

- macOS / Linux: `bash scripts/dingtalk-connection-check.sh --config ~/.openclaw/openclaw.json`
- Windows PowerShell: `pwsh -File scripts/dingtalk-connection-check.ps1 -Config ~/.openclaw/openclaw.json`
  - 旧版 Windows 可使用：`powershell.exe -File scripts/dingtalk-connection-check.ps1 -Config $env:USERPROFILE\.openclaw\openclaw.json`

完整排障流程：
- 英文版：[docs/connection-troubleshooting.md](docs/connection-troubleshooting.md)
- 中文版：[docs/connection-troubleshooting.zh-CN.md](docs/connection-troubleshooting.zh-CN.md)

如果新日志里出现 `connect.open` 或 `connect.websocket`，也可以直接按文档中的阶段说明来判断：前者优先查钉钉应用配置，后者优先查 WSS / 代理 / 企业网关。

关键设置清单（钉钉后台）
- 应用为企业内部应用/机器人，且已“发布”版本（不是草稿）
- 版本管理 → 已发布 → 版本详情：可见范围需为“全员员工”
- 已开启“机器人能力”，消息接收方式为“Stream 模式”

### 错误 payload 日志规范（`[ErrorPayload]`）

为便于快速定位 4xx/5xx 参数问题，插件会在 API 错误分支输出统一格式日志：

- 通用前缀：`[DingTalk][ErrorPayload][<scope>]`
- AI Card 前缀：`[DingTalk][AICard][ErrorPayload][<scope>]`
- 内容格式：`code=<...> message=<...> payload=<...>`（同时保留脱敏后的完整 payload）

常见 scope 示例：

- `send.proactiveMessage` / `send.proactiveMedia` / `send.message`
- `outbound.sendText` / `outbound.sendMedia`
- `inbound.downloadMedia` / `inbound.cardFinalize`
- `card.create` / `card.stream` / `card.stream.retryAfterRefresh`
- `retry.beforeDecision`

排查建议：

```bash
openclaw logs | grep "\[ErrorPayload\]"
```

如果你看到 `code=invalidParameter`，通常优先检查请求 payload 的必填字段（例如 `robotCode`、`userIds`、`msgKey`、`msgParam`）是否完整且格式正确。

## 开发指南

### 首次设置

1. 克隆仓库并安装依赖

```bash
git clone https://github.com/soimy/openclaw-channel-dingtalk.git
cd openclaw-channel-dingtalk
npm install
```

2. 验证开发环境

```bash
npm run type-check              # TypeScript 类型检查
npm run lint                    # ESLint 代码检查
```

### 常用命令

| 命令                 | 说明                |
| -------------------- | ------------------- |
| `npm run type-check` | TypeScript 类型检查 |
| `npm run lint`       | ESLint 代码检查     |
| `npm run lint:fix`   | 自动修复格式问题    |

### 项目结构

```
src/
  channel.ts           - 插件定义和辅助函数（535 行）
  runtime.ts           - 运行时管理（14 行）
  types.ts             - 类型定义（30+ interfaces）

index.ts              - 插件注册（29 行）
utils.ts              - 工具函数（110 行）

openclaw.plugin.json  - 插件配置
package.json          - 项目配置
README.md             - 本文件
```

### 代码质量

- **TypeScript**: 严格模式，0 错误
- **ESLint**: 自动检查和修复
- **Type Safety**: 完整的类型注解（30+ 接口）

### 类型系统

核心类型定义在 `src/types.ts` 中，包括：

```typescript
// 配置
DingTalkConfig; // 插件配置
DingTalkChannelConfig; // 多账户配置

// 消息处理
DingTalkInboundMessage; // 收到的钉钉消息
MessageContent; // 解析后的消息内容
HandleDingTalkMessageParams; // 消息处理参数

// AI 互动卡片
AICardInstance; // AI 卡片实例
AICardCreateAndDeliverRequest; // 创建并投放卡片请求
AICardStreamingRequest; // 流式更新请求
AICardStatus; // 卡片状态常量

// 工具函数类型
Logger; // 日志接口
RetryOptions; // 重试选项
MediaFile; // 下载的媒体文件
```

### 公开 API

插件导出以下低级 API 函数，可用于自定义集成：

```typescript
// 文本/Markdown 消息
sendBySession(config, sessionWebhook, text, options); // 通过会话发送

// AI 互动卡片
createAICard(config, conversationId, log); // 创建并投放 AI 卡片
streamAICard(card, content, finished, log); // 流式更新卡片内容
finishAICard(card, content, log); // 完成并关闭卡片

// 自动模式选择
sendMessage(config, conversationId, text, options); // 根据配置自动选择（含卡片/文本回退）

// 主动媒体发送
uploadMedia(config, mediaPath, mediaType, log); // 上传媒体并返回 media_id
sendProactiveMedia(config, target, mediaPath, mediaType, options); // 发送图片/语音/视频/文件

// 认证
getAccessToken(config, log); // 获取访问令牌
```

**使用示例：**

```typescript
import {
  createAICard,
  finishAICard,
  sendProactiveMedia,
  streamAICard,
} from './src/channel';

// 创建 AI 卡片
const card = await createAICard(config, conversationId, log);

// 流式更新内容
for (const chunk of aiResponseChunks) {
  await streamAICard(card, currentText + chunk, false, log);
}

// 完成并关闭卡片
await finishAICard(card, finalText, log);

// 主动发送图片
await sendProactiveMedia(config, 'cidxxxxxxxx', '/absolute/path/to/photo.png', 'image', {
  accountId: 'default',
  log,
});
```

### 架构

插件遵循 Telegram 参考实现的架构模式：

- **index.ts**: 最小化插件注册入口
- **src/channel.ts**: 所有 DingTalk 特定的逻辑（API、消息处理、配置等）
- **src/runtime.ts**: 运行时管理（getter/setter）
- **src/types.ts**: 类型定义
- **utils.ts**: 通用工具函数

## 测试

项目已基于 Vitest 初始化自动化测试，目录结构如下：

```text
tests/
  unit/
    sign.test.ts               # HmacSHA256 + Base64 签名测试
    message-transform.test.ts  # 文本/Markdown 消息转换测试
  integration/
    send-lifecycle.test.ts     # 插件 outbound.sendText 生命周期适配测试
```

### 运行测试

```bash
# 安装依赖（pnpm）
pnpm install

# 运行全部测试
pnpm test

# 生成覆盖率报告（coverage/）
pnpm test:coverage
```

### Mock 约束

- 所有测试中的网络请求均通过 `vi.mock('axios')` 拦截，禁止真实调用钉钉 API。
- 集成测试通过模块 mock 隔离 `openclaw/plugin-sdk`、`dingtalk-stream` 等外部依赖。

## 许可

[MIT](LICENSE)
