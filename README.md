# Ming (明)

> 知常曰明，不知常，妄作凶 —— 《道德经》

Ming 是一个本地运行的、model-agnostic 的 Agent 原型。当前版本重点验证核心链路：工具调用、四层 Context、认知路由、α/β/γ 对抗分析、T1/T3 核验、显式记忆、Automaticity、Experience Pool、Web Research、权限门禁、工具进展评估和每轮运行记录。

当前状态不是完整“自主进化 Agent”，而是可体验的工程版本。P5 主动性状态机、持久 Context、完整 Dreaming、完整 Git 回滚、MCP/Skills 生态接入仍属于后续阶段。

## 安装

```powershell
cd D:\Ming
python -m venv .venv
.\.venv\Scripts\activate
pip install -e .[dev]
```

## 配置

推荐创建 `config/local.yaml`，这个文件已被 `.gitignore` 忽略：

```yaml
llm:
  model: "deepseek/deepseek-chat"
  fallback_models:
    - "openai/gpt-4o-mini"
  api_key: "your-api-key"
```

也可以用环境变量：

```powershell
$env:MING_LLM_API_KEY="your-api-key"
$env:MING_LLM_MODEL="deepseek/deepseek-chat"
```

## 使用

单轮请求：

```powershell
python -m ming "帮我创建一个 hello.py 并运行它"
```

交互模式：

```powershell
python -m ming
```

帮助：

```powershell
python -m ming --help
```

交互命令：

| 命令 | 作用 |
|------|------|
| `/quit` | 退出 |
| `/clear` | 清空当前对话 |
| `/status` | 查看 token 估算、记忆数量、行为模式数量 |
| `/debug` | 切换 debug 日志 |
| `/compact` | 手动触发旧对话压缩 |
| `/rewind` | 移除最近一轮对话上下文 |
| `/rollback` | 回滚最近一次 `file_write` / `file_edit` 造成的文件变更 |
| `/forget session` | 从当前进程上下文移除 session 层 |
| `/forget memory` | 删除持久用户记忆 |
| `/forget project` | 删除持久 project 类型记忆 |
| `/trace` | 查看最近一轮 trace 文件路径 |
| `/checkpoint` | 查看最近 checkpoint 文件路径 |
| `/details` | 切换 agent-loop 进度详情展示 |

## 当前已实现

### Agent Loop

`Agent` 会循环执行：LLM 推理 → tool call → 工具执行 → 工具结果回喂 → 继续推理，直到模型不再请求工具。内置工具包括：

- `bash`：执行 shell 命令。Windows 下使用默认 shell，通常是 `cmd.exe`，优先使用 `dir`、`type`、`cd /d`、`python` 等 Windows 兼容命令。
- `file_read`：读取文件并带行号返回。
- `file_write`：创建或覆盖文件。
- `file_edit`：用唯一精确字符串替换编辑文件。
- `web_search`：搜索网页，返回结构化 `title/url/snippet/score`。
- `web_fetch`：抓取 URL 并提取可读正文。

`web_search` 支持按环境变量选择 provider：

- `TAVILY_API_KEY`：优先使用 Tavily。
- `EXA_API_KEY`：其次使用 Exa。
- 无 API key：回退到 DuckDuckGo Lite HTML 解析。

联网研究时优先使用 `web_search` + `web_fetch`，不要用 `bash` 硬爬搜索页。

CLI 默认展示高信号进度，例如“准备上下文”“调用模型”“执行工具 file_write”“执行 T3 核验”。底层 LiteLLM、httpx、asyncio 等 provider 日志默认不刷屏；需要看详细内部日志时使用 `/debug`，需要展开每步参数时使用 `/details`，完整记录仍可通过 `/trace` 查看。

### 动态工具选择

Ming 会根据用户输入动态缩小暴露给模型的工具集合，减少 tool schema 噪音：

- 搜索、网页、URL 类请求优先暴露 `web_search`、`web_fetch` 和少量必要文件工具。
- 显式“记住……”类请求默认不暴露外部工具，避免无意义工具调用。
- 普通工程任务仍可使用本地文件和 shell 工具。

### PermissionGate

`PermissionGate` 是真正的工具门禁，和认知路由 `Gate` 分开。当前会阻断高风险 shell 命令，例如 `git reset --hard`、`git push --force`、`rm -rf`、`rmdir /s`、`format`、`drop database` 等。

被阻断的工具调用会以 `[Permission denied]` 形式回喂给模型，让模型换成可撤销、可审查的方案。当前版本还没有交互式审批弹窗；需要危险操作时，应由用户明确手动执行或后续接入审批机制。

### T1/T3 核验

- 非工具型回答会在输出前跑一次 T1 CoVe 自检。
- 使用工具生成或修改工件后，会跑一次 T3 fresh-context 核验，检查工具结果是否支持最终答复。

### ToolEvent + ProgressAssessment

每次工具调用会生成 `ToolEvent`，记录工具名、状态、输出长度、证据数量和进展类型。`ProgressAssessment` 会判断这一步是否推进任务：

- `new_evidence`：拿到了有效证据。
- `no_signal`：空输出、短输出或失败。
- `artifact_noise`：产生大量内容但没有结构化证据。
- `unknown`：有输出但还无法判断。

连续多次 `no_signal/artifact_noise` 会停止本轮工具循环，避免换关键词、爬 HTML、读大文件这类策略循环。

这些事件会保存到 `.ming/traces/<turn_id>.json`，方便复盘 agent-loop 每轮到底做了什么。

### TODO / Notepad / Checkpoint

每轮请求会自动生成一份轻量运行工作台：

- `.ming/scratch/<turn_id>/notes.md`：记录用户请求和工具调用观察。
- `.ming/traces/<turn_id>.json`：记录工具事件、进展类型和最终输出。
- `.ming/checkpoints/<turn_id>/checkpoint.json`：保存当前消息、TODO、trace 路径和 notepad 路径。

当前 checkpoint 主要用于复盘和为后续断点续跑打基础；完整 resume 命令还在后续阶段。

### Error Recovery

Ming 在执行 `file_write` / `file_edit` 前会保存目标文件 snapshot：

- 如果文件原本存在，`/rollback` 会恢复旧内容。
- 如果文件是本轮新建，`/rollback` 会删除该文件。
- snapshot 存储在 `.ming/snapshots/`。

当前回滚只覆盖 Ming 文件工具造成的文本文件变更，不覆盖 `bash` 命令造成的外部副作用。

### 认知路由 + 对抗分析

每轮输入都会经过当前名为 `Gate` 的认知路由器。这里的 Gate 不是业界常说的审批/门禁，当前更准确地说是认知路由器；真正的工具门禁由 `PermissionGate` 负责。命中以下情况会升到对抗档：

- 不可逆操作，例如删除、强推、硬重置。
- 架构性修改。
- 多文件/跨模块影响。
- context 足够大。
- 用户显式要求再检查、对抗、independent review。
- Experience Pool 发现相似任务历史上出现过分歧。
- Automaticity 较低。

对抗档会并行运行 α/β 两个独立分析，再由 γ 比较并收敛。

### 记忆与经验

- 显式记忆：用户说“记住……”时会写入 `.ming/memory/*.md`。
- Automaticity：按行为模式维护熟练度，存储在 `.ming/automaticity.json`。
- Experience Pool：每轮记录 tier signal，存储在 `.ming/experience.jsonl`；相似任务如果历史上出现过分歧，会触发 Gate 的历史分歧规则。

### Context

Context 按基座层、会话层、对话层组织。超过阈值时会先裁剪旧工具输出，再用 LLM 压缩旧对话。

### LLM fallback

`llm.fallback_models` 会在主模型调用失败时按顺序尝试备用模型。fallback 是 turn-scoped，不会永久切换配置。

## 验证

运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

当前测试覆盖：

- 显式记忆写入。
- T1 输出前自检。
- T3 工具结果核验。
- LLM fallback。
- CLI help。
- Windows shell 描述。
- Experience Pool 历史分歧检索。
- Web search / fetch 结构化输出。
- ProgressAssessment 停止无增益工具循环。
- PermissionGate 阻断高风险 shell 命令。
- 动态工具选择。
- 每轮 trace/checkpoint/notepad/TODO 落盘。
- 默认日志不进入 debug 模式。
- 默认压制 LiteLLM/provider 控制台噪音，改用 agent-loop 缩略进度。
- `/details` 展开进度详情。
- `/forget session|memory|project` scope-aware 清理。
- `/rollback` 回滚最近一次文件工具变更。

## 用力测试场景

详见 [docs/experience-scenarios.md](docs/experience-scenarios.md)。建议先从“Windows 工具循环压力测试”和“对抗档架构审查”开始。

## 后续路线

完整路线见 [PLAN.md](PLAN.md)。后续重点包括：Error Recovery、Memory、Context 工作台深化、Observe/Trace 可视化、Checkpoint/Resume、低摩擦交互，以及谨慎接入 MCP/Skills。

## 项目结构

```text
src/ming/
├── cli.py
├── config.py
├── core/
│   ├── agent.py
│   ├── adversarial.py
│   ├── automaticity.py
│   ├── gate.py
│   ├── llm.py
│   ├── loop_detection.py
│   ├── notepad.py
│   ├── permission.py
│   ├── progress.py
│   ├── recovery.py
│   ├── todo.py
│   ├── tool_selection.py
│   └── trace.py
├── context/
│   └── manager.py
├── memory/
│   ├── experience.py
│   └── store.py
└── tools/
    ├── base.py
    ├── bash.py
    ├── file.py
    └── web.py
```
