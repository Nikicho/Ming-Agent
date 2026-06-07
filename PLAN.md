# Ming 当前实现计划与路线

> 写给后续开发者：本文记录真实工程状态和下一阶段路线，不把原型骨架标成完整完成。

## 当前版本目标

把 Ming 从“架构演示骨架”推进到“可日常体验的本地 Agent 原型”。当前重点是让核心 loop 可跑、可观测、可停止、可测试。

## 术语校准

### 认知路由与 Gate 命名

当前代码里的认知路由器已命名为 `CognitiveRouter`：根据风险、上下文、Automaticity、历史分歧等信号，决定走单核还是对抗分析。

业界常说的 gate 更常指 **审批、权限、门禁**，例如：

- 工具调用前审批。
- 不可逆操作拦截。
- 数据/网络/账号权限控制。
- human approval gate。

当前约定：

- `CognitiveRouter` 负责认知路由。
- `PermissionGate` 负责真正的权限门禁。
- `ming.core.gate.Gate` 保留为兼容 alias，不作为新文档和新代码的首选命名。

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

- `CognitiveRouter` 的 7 规则启发式路由。
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

已落地：

- L1 工具错误：`ErrorClassifier` 分类 transient/provider/tool_input/permission，标记 retryable/recoverable。
- L2 推理错误：T3 fail 后把失败原因回喂进主 loop，允许重新调用工具修正。
- L4 Provider 错误：LiteLLM fallback 已 turn-scoped 恢复，provider 噪音默认压制。
- L5 循环/退化：fingerprint + progress + budget + human handoff。
- 文件回滚：file write/edit 前快照，按 turn 保存 checkpoint，`/rollback` 回滚最近文件工具变更。
- 权限门禁：delete、force push、reset hard、写远程、账号操作 hard stop。

剩余增强：

- Git 回滚：可选每轮 checkpoint commit。
- L3 对抗系统错误：β/γ 超时降级、部分结果可用、用户可见的降级说明。
- 交互式审批：一键批准/拒绝高风险操作。

### 2. Memory 主线

目标：把“记住一句话”升级为可审计、可遗忘、可再验证的多类型记忆系统。

已落地：

- 显式记忆：用户说“记住……”进入 user memory。
- 会话摘要：可从消息中提取 user/project 类型记忆。
- 项目记忆：支持 project 类型记忆、scope 注入和删除。
- Experience Pool：失败、分歧、策略效果和工具有效性。
- Automaticity：行为模式熟练度，不是单次任务分数。
- NotePad：每轮运行中的 scratch notes，记录关键假设、发现、未解决问题。
- 待复核记忆：支持 stale/stale_reason 标记、context 标注和注入降权。
- 清空 Memory：提供按 scope 清空，而不是一键删所有。

剩余增强：

- Dreaming：已落地 Light 非破坏性审阅报告；Deep/REM 巩固、降噪、合并、遗忘仍未做。
- 待复核记忆后续增强：检索时自动重新验证、更新或删除。

建议 scope：

- `turn`：当前轮临时信息。
- `session`：当前会话。
- `project`：当前仓库。
- `user`：用户偏好。
- `global`：跨项目经验。

### 3. Context Management 主线

目标：让 Context 不只是“拼 message”，而是可控的信息工作台。

已落地：

- `ContextAssembler`：显式输入 base/session/dialog/instant/notepad/toolset/pinned evidence。
- 动态拼接 Prompt：每轮注入 instant context、TODO、Notepad、pinned evidence、toolset。
- TODO 管理：把多步请求拆成可观察 checklist，并随工具执行推进。
- 动态选择 Tool：根据任务和阶段选择暴露的工具 schema，减少干扰。
- NotePad：记录用户请求、assumptions、evidence、blockers、tool observations。
- 压缩机制：tool pruning、结构化摘要、关键证据保留、压缩后校验。
- 作用域切换：`/scope user,project,global` 控制记忆注入范围。
- 清空 Memory/Context：区分 `/clear`、`/forget session`、`/forget memory`、`/forget project`。
- 持久 Context：跨 session 加载 user/project/global memory；`/resume` 可恢复最近 checkpoint context。

剩余改进：

- TODO 拆分仍是启发式规则，后续可接 LLM planner。
- `/resume` 当前恢复最近 checkpoint 的上下文，尚未支持指定 checkpoint_id。

### 4. Web Research 主线

目标：让联网研究变成可引用、可停止、可复现的证据链，而不是 bash 爬网页。

已落地：

- `web_search`。
- `web_fetch`。
- `web_research`：search + source selection + fetch + evidence pack。
- 引用追踪：evidence pack 返回 citations 和 source_url。
- domain allow/deny。
- freshness filter。
- `.ming/scratch/` evidence pack 缓存。
- 连续无增益停止。

剩余增强：

- PDF/HTML 正文抽取增强。
- 更强 source ranking 和引用格式约束。

### 5. Observe / Trace / Feedback 主线

目标：让每轮执行可观察、可复盘、可反馈到记忆与 Automaticity。

已落地：

- `RunTrace`：记录 turn_id、tool events、observations、assessments。
- `Observe` 链路：工具结果提取 evidence/blocker/observation 进入 notepad 和 trace。
- UI tool cards：默认展示摘要，`/details` 和 `/expand <event_id>` 展开 raw detail。
- `/trace`：查看当前轮/上一轮 trace。
- `/expand <event_id>`：展开某个工具调用细节。
- 结构化 event log：默认 INFO，DEBUG 显式开启。

剩余增强：

- Feedback 闭环：人类显式反馈、T4/T6/T7 更细地写入 Experience。
- Trace 可视化 UI。

### 6. Persist Checkpoint / 断点续跑主线

目标：长任务中断后能恢复，不依赖完整对话窗口。

已落地：

- 每轮 checkpoint：messages summary、TODO、notepad、tool events、changed files。
- 文件快照：编辑前保存 patch 或副本。
- 断点续跑：`/resume` 和 `/resume <checkpoint_id>`。
- checkpoint 清理策略：按时间、项目、成功状态清理。

剩余增强：

- 崩溃恢复：启动时提示是否恢复未完成 run。
- checkpoint cleanup 继续增强为按项目/成功状态清理。

### 7. 低摩擦交互主线

目标：减少人类管理 Agent 的成本，让用户只在高价值节点介入。

已落地：

- 默认展示高信号进度，不刷屏。
- `/cleanup` 清理旧 checkpoint。
- 自动命名 checkpoint。

剩余增强：

- 后台自动整理 logs/scratch/checkpoints。
- 轻量主动提示：只在价值大于打断成本时出现。
- 一键批准/拒绝/展开/暂停/继续。
- 常用工作流自动化：测试、审查、清理、总结。
- 多入口适配：CLI、桌面、语音/低摩擦通道预留。

### 8. MCP / Skills 谨慎接入主线

目标：接入生态，但不污染核心认知层。

原则：

- MCP Adapter 延后到明确需要外部工具时再接。
- 已落地 Skill Index，只加载 name/description/trust_level/allowed_tools。
- Skill body 只在认知路由和权限门禁都允许时加载。
- 已落地 Tool Need Proposal，新工具注册必须经过测试和人类批准。
- 第三方 skill 默认低信任，不得改写 P/D/E 核心原则。

## 近期优先级建议

1. **重命名与门禁拆分**
   - 已把当前 `Gate` 实现改为 `CognitiveRouter`，并保留兼容 alias。
   - 已落地 `PermissionGate` 基础门禁。

2. **Context 工作台**
   - 已落地 `TodoState`。
   - 已落地 `NotepadStore`。
   - 已落地 `ToolSelector`。
   - 已落地 scope-aware `/clear` 与 `/forget session|memory|project`。

3. **Trace + Checkpoint**
   - 已落地 `RunTrace`。
   - 已落地 `.ming/checkpoints/<turn_id>/`。
   - 已落地 `/trace` 与 `/checkpoint`。
   - 已落地 `/resume` 最近 checkpoint 基础恢复。

4. **Error Recovery 实装**
   - 已落地 `file_write` / `file_edit` 前 snapshot。
   - 已落地 `/rollback` 回滚最近一次文件工具变更。
   - T3 fail 后重入 loop。

5. **Memory 升级**
   - 会话摘要自动提取。
   - project memory。
   - 待复核记忆标记、降权和 Dream Light 审阅报告。

6. **低摩擦交互**
   - 已落地默认缩略 agent-loop 进度。
   - 已落地 `/details` 展开进度详情。
   - 已压制默认 LiteLLM/provider 控制台噪音。

## 验证命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
python -m ming --help
```
