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

运行中如果需要停止当前一轮思考，按 `Ctrl+C`。Ming 会把本轮标记为已停止，保存 trace/checkpoint，然后回到输入提示；再次按 `/quit` 可退出。

帮助：

```powershell
python -m ming --help
```

Ming Agent Workbench 本地可视化界面：

```powershell
python -m ming ui --port 8765
```

打开 `http://127.0.0.1:8765` 后，可以查看最近一轮 agent-loop 的任务、TODO、工具步骤、进展判断、可公开思路摘要、subagent 状态和 SessionTrace/checkpoint/notepad 路径。当前 UI 读取本地 `.ming/` 运行产物，不需要 API key，也不会展示底层 LiteLLM/provider 噪音日志。

Dream 轻量审阅报告：

```powershell
python -m ming dream
```

Dream 当前是非破坏性的离线整理器：读取 `.ming/session_traces/`、`.ming/checkpoints/` 和 `.ming/memory/`，生成 `.ming/dreams/<timestamp>_light.json`。它只提出任务摘要、project lessons、待复核记忆和重复记忆候选，不会自动改写记忆。

交互命令：

| 命令 | 作用 |
|------|------|
| `/quit` | 退出 |
| `/clear` | 清空当前对话 |
| `/status` | 查看 token 估算、记忆数量、行为模式数量 |
| `/debug` | 切换 debug 日志 |
| `/compact` | 手动触发旧对话压缩 |
| `/resume` | 从最近 checkpoint 恢复上下文 |
| `/resume <checkpoint_id>` | 从指定 checkpoint 恢复上下文 |
| `/rewind` | 移除最近一轮对话上下文 |
| `/rollback` | 回滚最近一次 `file_write` / `file_edit` 造成的文件变更 |
| `/forget session` | 从当前进程上下文移除 session 层 |
| `/forget memory` | 删除持久用户记忆 |
| `/forget project` | 删除持久 project 类型记忆 |
| `/scope user,project,global` | 切换当前注入上下文的记忆作用域 |
| `/expand <event_id>` | 展开最近 trace 中某个事件 |
| `/cleanup` | 清理旧 checkpoint |
| `/dream` | 生成一次非破坏性的 Dream 审阅报告 |
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
- `web_research`：搜索、筛选来源、抓取网页并生成 evidence pack/citations。

`web_search` 支持按环境变量选择 provider：

- `TAVILY_API_KEY`：优先使用 Tavily。
- `EXA_API_KEY`：其次使用 Exa。
- 无 API key：回退到 DuckDuckGo Lite HTML 解析。

联网研究时优先使用 `web_research` 或 `web_search` + `web_fetch`，不要用 `bash` 硬爬搜索页。`web_research` 支持 domain allow/deny、freshness filter，并把 evidence pack 缓存到 `.ming/scratch/`。

CLI 默认展示高信号进度，例如“准备上下文”“调用模型”“执行工具 file_write”“执行 T3 核验”。底层 LiteLLM、httpx、asyncio 等 provider 日志默认不刷屏；需要看详细内部日志时使用 `/debug`，需要展开每步参数时使用 `/details`，完整记录仍可通过 `/trace` 查看。
如果模型调用失败或用户按 `Ctrl+C` 停止当前轮，Ming 会优雅收口并落盘失败/停止 trace，而不是把 Python traceback 刷到用户界面。

### Ming Agent Workbench

`python -m ming ui` 会启动一个本地 Web UI，默认监听 `127.0.0.1:8765`。页面可以直接输入任务并通过 `/api/chat` 发起一轮 Ming 执行，也可以用 Stop 按钮调用 `/api/turns/current/stop` 停止当前轮。它把 `.ming/session_traces/`、`.ming/checkpoints/` 和 `.ming/live/events.jsonl` 整理成更接近产品体验的任务工作台：

- 左侧会话栏：默认收起，只在需要切换/查看最近会话时展开，降低常驻干扰。
- 中央工作区：保留对话流、输入框和“停止思考”按钮，Enter 发送，Shift+Enter 换行。
- 右侧过程面板：跟随当前任务展示 Agent 状态、执行过程、TODO、Artifacts、Context/Token、可公开思考摘要、Subagents 和 SSE 实时事件。
- 详情弹窗：通过“做了什么 / 异常原因 / SessionTrace / 设置与模型”四个页签查看结构化细节。工具卡默认折叠，只有点击时展开参数和 raw detail；对抗分析只在需要人类判断时展示“观点 A/B + 裁决”。

新打开的页面默认只接收打开之后的 live event，不会把历史 `events.jsonl` 重新灌进对话区；历史运行记录仍通过最近 `SessionTrace` / checkpoint 的执行过程和诊断详情查看。当前还没有 provider token 级逐字流式输出，也还不是 React/Tauri 版本；Web UI 已能发起、停止本地单轮任务，并展示 agent-loop 级别的 SSE 过程。

会话记录保存在本地 `.ming/`：

- `.ming/checkpoints/<turn_id>/checkpoint.json`：保存本轮消息、TODO、trace/notepad 路径和摘要，可用于 `/resume`。
- `.ming/session_traces/<session_id>.json`：保存 `ming-trace-v1` 会话级结构化 trace，包括路由、步骤、工具调用、T1/T3、对抗分支、反馈和成本指标。
- `.ming/live/events.jsonl`：保存 Web UI/CLI 可消费的高信号 SSE 事件流。
- `.ming/scratch/<turn_id>/notes.md`：保存本轮 notepad、证据和 blocker。

SSE 排障：
- 页面还是旧版样式：确认 `python -m ming ui --port 8765` 加载的是当前 `D:\Ming` 源码；必要时在项目根目录执行 `python -m pip install -e .`，然后重启服务并 `Ctrl+F5` 强刷浏览器。
- Live Events 不更新：打开 `http://127.0.0.1:8765/api/events`，应能看到 `text/event-stream` 输出；如果没有，检查 8765 是否被旧进程占用。
- Stop 后重复取消提示：当前 runtime 会去重同一 turn 的 `cancelled` live event，若仍重复，优先检查是否同时开了多个 UI 服务。
- 敏感信息：live event 会对 `api_key=...`、`Authorization: Bearer ...`、`sk-...` 这类 key-like 内容做脱敏；`config/local.yaml` 仍不要提交。

已知限制：
- 当前是 stdlib HTML MVP，不是 React/Tauri 最终界面。
- SSE 只流式展示 agent-loop 事件和最终结果，不是 provider token 级逐字流。
- 同一个 Web UI 服务进程一次只运行一个 active turn；第二个提交会返回 busy。
- live event 日志默认保留最近 1000 条，避免 `.ming/live/events.jsonl` 无限增长。

### 动态工具选择

Ming 会根据用户输入动态缩小暴露给模型的工具集合，减少 tool schema 噪音：

- 搜索、网页、URL 类请求优先暴露 `web_search`、`web_fetch` 和少量必要文件工具。
- 本地页面、HTML、文件生成类请求优先暴露 `file_write`、`file_edit`、`file_read` 和 `bash`，减少无关 web 工具带来的工具选择噪音。
- 显式“记住……”类请求默认不暴露外部工具，避免无意义工具调用。
- 普通工程任务仍可使用本地文件和 shell 工具。

### PermissionGate

`PermissionGate` 是真正的工具门禁，和认知路由 `CognitiveRouter` 分开。当前会阻断高风险 shell 命令，例如 `git reset --hard`、`git push --force`、`rm -rf`、`rmdir /s`、`format`、`drop database` 等。

被阻断的工具调用会以 `[Permission denied]` 形式回喂给模型，让模型换成可撤销、可审查的方案。当前版本还没有交互式审批弹窗；需要危险操作时，应由用户明确手动执行或后续接入审批机制。

### T1/T3 核验

- 非工具型回答会在输出前跑一次 T1 CoVe 自检。
- 使用工具生成或修改工件后，会跑一次 T3 fresh-context 核验，检查工具结果是否支持最终答复。
- 如果 T3 判定最终答复和工具证据不一致，Ming 会把失败原因回喂进主 loop，允许重新调用工具修正一次。

### ToolEvent + ProgressAssessment

每次工具调用会生成 `ToolEvent`，记录工具名、状态、输出长度、证据数量和进展类型。`ProgressAssessment` 会判断这一步是否推进任务：

- `new_evidence`：拿到了有效证据。
- `no_signal`：空输出、短输出或失败。
- `artifact_noise`：产生大量内容但没有结构化证据。
- `unknown`：有输出但还无法判断。

连续多次 `no_signal/artifact_noise` 会暂停本轮工具循环，避免换关键词、爬 HTML、读大文件这类策略空转。用户界面会显示“为什么暂停、刚才主要尝试了哪些工具、下一步如何继续”；原始 `no_signal` 诊断保留在 trace/notepad 详情中。

这些事件会保存到 `.ming/session_traces/<session_id>.json`，方便复盘 agent-loop 每轮到底做了什么。
Trace 还会记录 observations 和 assessments；交互模式用 `/trace` 可以查看最近 SessionTrace 文件路径。

### 测试与评测架构

Ming 当前补齐了测试架构文档里的基础工程入口：

- `SessionTrace` 采用 `ming-trace-v1`，作为调试、回放、回归测试和成本分析的统一输入。
- `ming.eval.judges.select_judges_for_turn()` 会按 trace shape 选择必要 judge：默认 `gate_judge`，工具轮增加 `tool_use_judge`，对抗轮增加 `gamma_output_judge` 和 `adversarial_value_judge`，压缩事件增加 `compaction_judge`。
- `ming.eval.golden.load_golden_conversation()` 支持加载 golden conversation YAML，作为后续 scenario replay 的标准格式。
- `ming.eval.fingerprint.behavior_fingerprint()` 对路由、工具序列、循环状态、上限命中和 tier signal 生成稳定行为指纹。
- `ming.eval.cost.summarize_trace_budget()` 汇总 LLM calls、tokens 和成本，并标记是否超过预算。

### Context 工作台

Context 由 `ContextAssembler` 显式组装，顺序是 base → session → instant → TODO → Notepad → pinned evidence → toolset → dialog。每轮请求会自动生成一份轻量运行工作台：

- instant context：记录当前用户请求、风险/工具相关的本轮指令。
- TODO：把多步请求拆成 checklist，并在工具执行后推进状态。
- `.ming/scratch/<turn_id>/notes.md`：记录用户请求和工具调用观察。
- `.ming/session_traces/<session_id>.json`：记录路由、步骤、工具事件、进展类型、反馈指标和最终输出。
- `.ming/checkpoints/<turn_id>/checkpoint.json`：保存当前消息、TODO、trace 路径和 notepad 路径。
- pinned evidence：压缩时强制保留关键证据，并校验摘要是否保留。
- scope context：`/scope user,project,global` 控制 user/project/global 记忆是否注入 session layer。

`/resume` 可以从最近 checkpoint 恢复上下文，继续在当前 CLI 进程里使用。
checkpoint 同时保存 messages summary、changed files、name，并支持 `/resume <checkpoint_id>` 和 `/cleanup`。

### Error Recovery

Ming 在执行 `file_write` / `file_edit` 前会保存目标文件 snapshot：

- 如果文件原本存在，`/rollback` 会恢复旧内容。
- 如果文件是本轮新建，`/rollback` 会删除该文件。
- snapshot 存储在 `.ming/snapshots/`。

当前回滚只覆盖 Ming 文件工具造成的文本文件变更，不覆盖 `bash` 命令造成的外部副作用。
错误恢复还包括 `ErrorClassifier`：区分 transient、provider、tool_input、permission 等错误，标记 retryable/recoverable，并用于后续恢复策略。LLM/provider timeout 会被转换为用户可理解的“模型服务长时间没有响应”说明，原始 `litellm` / provider 异常只保存在 trace、notepad 和诊断详情里，不再默认刷到对话区。

工具错误会进一步区分普通无进展和 Ming 内部执行策略失败：

- `tool_input_error`：工具参数格式错误，例如 malformed JSON、unterminated string。
- `tool_strategy_error`：工具选择或调用方式不可靠，例如用超长 `bash` 命令写大段文件。

连续出现这类错误时，Ming 会先进入一次 `replan`：把诊断注入回 agent-loop，要求改用合法 JSON 的 `file_write/file_edit` 或分块写入。只有重规划后仍然没有进展，才暂停本轮，并把问题归因为“工具调用格式或写入策略失败”，而不是要求用户补材料。

### 认知路由 + 对抗分析

每轮输入都会经过 `CognitiveRouter` 认知路由器。它不是业界常说的审批/门禁，而是根据任务风险、上下文、Automaticity 和历史分歧决定走单核还是对抗分析；真正的工具门禁由 `PermissionGate` 负责。命中以下情况会升到对抗档：

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
- 会话摘要提取：可从会话消息中提取 user/project 类型记忆。
- 待复核记忆：旧事实可标记 `stale` / `stale_reason`，注入 context 时会标注“待复核记忆”并排在高置信记忆之后。
- Automaticity：按行为模式维护熟练度，存储在 `.ming/automaticity.json`。
- Experience Pool：每轮记录 tier signal，存储在 `.ming/experience.jsonl`；相似任务如果历史上出现过分歧，会触发认知路由器的历史分歧规则。

### Dream

`DreamEngine` 是轻量记忆巩固器的第一版。它不会后台神秘改写“大脑”，而是手动触发、只读扫描、生成审阅报告：

- 最近任务摘要：turn、工具数量、checkpoint changed files。
- project lessons：从最近结果和 changed files 中提取可沉淀线索。
- 待复核记忆候选：列出 stale memory 及 stale_reason。
- 重复记忆候选：按 type + description 查找可合并项。
- next actions：给出需要人类确认的整理动作。

### Skill Index / Tool Need

Ming 支持 metadata-only 的 `SkillIndex`：只加载 name、description、trust_level、allowed_tools，不把 skill body 常驻注入大脑。Agent 可以生成 `ToolNeedProposal`，但新工具注册仍需要测试和人类批准。

### Context 压缩

超过阈值时会先裁剪旧工具输出，再用 LLM 压缩旧对话。压缩提示会带上 pinned evidence；压缩后会检查摘要是否保留关键证据，缺失时把证据补回摘要。

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
- Web research evidence pack、domain allow/deny、freshness filter。
- ProgressAssessment 停止无增益工具循环。
- PermissionGate 阻断高风险 shell 命令。
- 动态工具选择。
- 本地页面生成类任务的工具集收敛。
- 每轮 trace/checkpoint/notepad/TODO 落盘。
- ContextAssembler 显式组装 context。
- instant layer / TODO / Notepad / toolset 注入。
- pinned evidence 和压缩后校验。
- `/scope user,project,global` 作用域切换。
- `/resume` 从最近 checkpoint 恢复上下文。
- `/resume <checkpoint_id>` 指定恢复。
- `/expand <event_id>` 展开 trace event。
- checkpoint cleanup。
- ErrorClassifier 与 T3 fail 重入 loop。
- session/project memory extract 与待复核记忆标记、降权和 context 标注。
- `CognitiveRouter` 认知路由命名与旧 `Gate` 兼容导出。
- Dream 非破坏性审阅报告。
- SkillIndex 与 ToolNeedProposal。
- 默认日志不进入 debug 模式。
- 默认压制 LiteLLM/provider 控制台噪音，改用 agent-loop 缩略进度。
- 模型调用失败和 `Ctrl+C` 停止当前轮时优雅落盘，不刷 traceback。
- `/details` 展开进度详情。
- `/forget session|memory|project` scope-aware 清理。
- `/rollback` 回滚最近一次文件工具变更。
- Ming Agent Workbench 三栏 UI、状态聚合、HTML 渲染、空状态、`/api/events` SSE live event、`/api/chat` 和停止当前轮。
- 测试架构基础接口：judge 选择、golden conversation YAML、行为指纹和成本预算摘要。

## 用力测试场景

详见 [docs/experience-scenarios.md](docs/experience-scenarios.md)。建议先从“Windows 工具循环压力测试”和“对抗档架构审查”开始。

体验 Ming Agent Workbench 时可以按这个顺序压测：

1. 启动 `python -m ming ui --port 8765`，打开 `http://127.0.0.1:8765`，确认 conversation、输入框、Stop、Live Events 和最近一轮 trace 都能正常展示。
2. 在页面输入“创建 `scratch/webui_demo.txt`，内容写入 hello，然后读取确认”。确认 Conversation 里出现用户消息、运行事件和最终回复；右侧 Live Events 实时出现 `submitted`、`context`、`llm`、`tool`、`verify`、`done`、`final`。
3. 再输入一个较慢或容易重复的任务，例如“反复检查同一个不存在的文件直到找到答案”，运行中点击 Stop。确认页面出现 `cancelled`，按钮恢复可用，下一条消息可以继续发送。
4. 在另一个终端运行 `python -m ming`，输入“创建 `scratch/trace_demo.txt`，内容写入你刚才的建议，然后读取确认”。回到页面确认右侧 Live Events 也能显示 CLI 发起的 agent-loop。
5. 输入一个需要联网证据的问题，例如“搜索 Ming agent web_search 的设计参考，给出来源摘要”。确认页面能看到 `web_search` / `web_fetch` / `web_research` 相关工具事件和 evidence 数量。

## 后续路线

完整路线见 [PLAN.md](PLAN.md)。后续重点包括：更完整的 Git 回滚、交互式审批、Dreaming 巩固、完整 MCP Adapter，以及更强的 PDF 抽取。

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
│   ├── assembler.py
│   └── manager.py
├── memory/
│   ├── experience.py
│   └── store.py
├── skills/
│   └── index.py
└── tools/
    ├── base.py
    ├── bash.py
    ├── file.py
    └── web.py
```
