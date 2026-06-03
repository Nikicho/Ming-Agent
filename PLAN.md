# Ming 当前实现计划与路线

> 写给后续开发者：本文记录真实工程状态和下一阶段路线，不把原型骨架标成完整完成。

## 当前版本目标

把 Ming 从“架构演示骨架”推进到“可日常体验的本地 Agent 原型”。当前重点是让核心 loop 可跑、可观测、可停止、可测试。

## 术语校准

### Gate 命名问题

当前代码里的 `Gate` 指的是“认知路由器”：根据风险、上下文、Automaticity、历史分歧等信号，决定走单核还是对抗分析。

业界常说的 gate 更常指 **审批、权限、门禁**，例如：

- 工具调用前审批。
- 不可逆操作拦截。
- 数据/网络/账号权限控制。
- human approval gate。

因此后续建议：

- 将当前 `Gate` 概念逐步改名为 `CognitiveRouter` 或 `RoutingGate`。
- 新增真正的 `PermissionGate` / `SafetyGate`，负责审批和门禁。
- 文档中明确区分“认知路由”和“权限门禁”，避免混用。

## 已落地

### P0：基础运行

- Python package / CLI 入口。
- LiteLLM 接入。
- YAML 配置：`config/default.yaml` + `config/local.yaml` + 环境变量。
- 文件日志：`.ming/logs/`。

### P1：Agent Loop 基础版

- 支持 tool call 循环。
- 内置 `bash`、`file_read`、`file_write`、`file_edit`。
- 支持迭代上限和墙钟超时。
- 支持重复 tool call 指纹检测。

### P2：Context 基础版

- 基座层、会话层、对话层组装。
- 旧工具输出 pruning。
- LLM 摘要式 compaction。
- CLI 手动 `/compact`。

### P3：认知路由 + Automaticity 基础版

- 现名 `Gate` 的 7 规则启发式路由。
- Automaticity 文件式存储。
- Tier signal 反馈更新。
- Experience Pool 记录历史分歧，并接入路由规则。

### P4：对抗分析基础版

- α/β 并行 LLM 调用。
- β 使用独立分析 prompt。
- γ fresh-context 比较。
- OPPOSED 时进入 γ 分歧厘清。

### P5：稳定性基础版

- LLM fallback：主模型失败时尝试 `fallback_models`。
- T1：非工具输出前 CoVe 自检。
- T3：工具工件输出后 fresh-context 核验。
- CLI `/rewind`。
- 显式记忆：“记住……”写入 `.ming/memory/`。
- Native `web_search` / `web_fetch`。
- `ToolEvent` + `ProgressAssessment`：连续无增益工具调用会停止本轮循环。
- 自动化测试覆盖核心编排。

## 建设主线

### 1. Error Recovery 主线

目标：让 Ming 在工具失败、模型失败、循环退化、文件误改时能恢复或停在可理解状态。

待建设：

- L1 工具错误：错误分类、短重试、错误摘要、可恢复/不可恢复标记。
- L2 推理错误：T1/T3 失败后的重入策略，而不是只记录 tier signal。
- L3 对抗系统错误：β/γ 超时降级、部分结果可用、用户可见的降级说明。
- L4 Provider 错误：fallback、限流、超时、模型不可用、turn-scoped 恢复。
- L5 循环/退化：fingerprint + progress + budget + human handoff。
- 文件回滚：file write/edit 前快照，按 turn 保存 checkpoint。
- Git 回滚：可选每轮 checkpoint commit，提供 `/rollback`。
- 权限门禁：delete、force push、reset hard、写远程、账号操作 hard stop。

### 2. Memory 主线

目标：把“记住一句话”升级为可审计、可遗忘、可再验证的多类型记忆系统。

待建设：

- 显式记忆：用户说“记住……”进入 user memory。
- 会话摘要：每轮或会话结束自动提取目标、决策、偏好、文件事实。
- 项目记忆：项目结构、约定、近期改动、常用命令。
- Experience Pool：失败、分歧、策略效果和工具有效性。
- Automaticity：行为模式熟练度，不是单次任务分数。
- NotePad：每轮运行中的 scratch notes，记录关键假设、发现、未解决问题。
- Dreaming：Light/Deep/REM 巩固、降噪、合并、遗忘。
- Stale memory reconsolidation：检索时重新验证，过期则降权、更新或删除。
- 清空 Memory：提供按 scope 清空，而不是一键删所有。

建议 scope：

- `turn`：当前轮临时信息。
- `session`：当前会话。
- `project`：当前仓库。
- `user`：用户偏好。
- `global`：跨项目经验。

### 3. Context Management 主线

目标：让 Context 不只是“拼 message”，而是可控的信息工作台。

方法论候选：

- 动态拼接 Prompt：按任务、风险、工具、记忆命中动态组装，而不是全量常驻。
- TODO 管理：把当前目标拆成可观察的 checklist，随执行更新。
- 动态选择 Tool：根据任务和阶段选择暴露的工具 schema，减少干扰。
- NotePad：记录关键事实、假设、证据、阻塞点，供压缩和续跑使用。
- 压缩机制：tool pruning、结构化摘要、关键证据保留、压缩后校验。
- 作用域切换：turn/session/project/user/global 的 context 切换和隔离。
- 清空 Memory/Context：区分 `/clear`、`/forget session`、`/forget project`。
- 持久 Context：跨 session 冷启动后的 seamless retrieval。

短期实现建议：

- 增加 `ContextAssembler`，显式输入 base/session/dialog/instant/notepad/toolset。
- 增加 `TodoState`，进入 agent-loop 前生成，执行中更新。
- 增加 `ToolSelector`，每轮只暴露相关工具。
- 增加 `NotepadStore`，默认保存在 `.ming/scratch/<turn_id>/notes.md`。

### 4. Web Research 主线

目标：让联网研究变成可引用、可停止、可复现的证据链，而不是 bash 爬网页。

已落地：

- `web_search`。
- `web_fetch`。
- 连续无增益停止。

待建设：

- `web_research`：search + source selection + fetch + evidence pack。
- 引用追踪：最终回答能追到 URL。
- domain allow/deny。
- freshness filter。
- PDF/HTML 正文抽取增强。
- `.ming/scratch/<turn_id>/` 缓存和自动清理。

### 5. Observe / Trace / Feedback 主线

目标：让每轮执行可观察、可复盘、可反馈到记忆与 Automaticity。

待建设：

- `RunTrace`：记录 turn_id、plan、tool events、observations、assessments。
- `Observe` 链路：每次工具结果进入观察层，先摘要再进入 LLM。
- `Feedback` 闭环：T1/T3/T4/T6/T7、人类反馈、工具错误都写入 Experience。
- UI tool cards：默认展示摘要，debug 才展开 raw output。
- `/trace`：查看当前轮/上一轮 trace。
- `/expand <event_id>`：展开某个工具调用细节。
- 结构化 event log：默认 INFO，DEBUG 显式开启。

### 6. Persist Checkpoint / 断点续跑主线

目标：长任务中断后能恢复，不依赖完整对话窗口。

待建设：

- 每轮 checkpoint：messages summary、TODO、notepad、tool events、changed files。
- 文件快照：编辑前保存 patch 或副本。
- 断点续跑：`ming resume <checkpoint_id>`。
- 崩溃恢复：启动时提示是否恢复未完成 run。
- checkpoint 清理策略：按时间、项目、成功状态清理。

### 7. 低摩擦交互主线

目标：减少人类管理 Agent 的成本，让用户只在高价值节点介入。

待建设：

- 默认展示高信号进度，不刷屏。
- 后台自动整理 logs/scratch/checkpoints。
- 轻量主动提示：只在价值大于打断成本时出现。
- 一键批准/拒绝/展开/暂停/继续。
- 自动命名会话和 checkpoint。
- 常用工作流自动化：测试、审查、清理、总结。
- 多入口适配：CLI、桌面、语音/低摩擦通道预留。

### 8. MCP / Skills 谨慎接入主线

目标：接入生态，但不污染核心认知层。

原则：

- MCP Adapter 延后到明确需要外部工具时再接。
- 先做 Skill Index，只加载 name/description/trust_level/allowed_tools。
- Skill body 只在认知路由和权限门禁都允许时加载。
- Agent 可提出 Tool Need Proposal，但新工具注册必须经过测试和人类批准。
- 第三方 skill 默认低信任，不得改写 P/D/E 核心原则。

## 近期优先级建议

1. **重命名与门禁拆分**
   - 把当前 `Gate` 文档改为 `CognitiveRouter`。
   - 新增 `PermissionGate` 设计。

2. **Context 工作台**
   - `TodoState`。
   - `NotepadStore`。
   - `ToolSelector`。
   - scope-aware clear/forget 命令设计。

3. **Trace + Checkpoint**
   - `RunTrace`。
   - `.ming/checkpoints/<turn_id>/`。
   - `/trace` 和 `/resume` 设计。

4. **Error Recovery 实装**
   - file snapshot。
   - `/rollback`。
   - T3 fail 后重入 loop。

5. **Memory 升级**
   - 会话摘要自动提取。
   - project memory。
   - stale memory reconsolidation。

## 验证命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
python -m ming --help
```
