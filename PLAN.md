# Ming 实现计划

> 技术栈：Python 3.12+ / LiteLLM / MCP / DeepSeek（开发） / Git-based 回滚
> 
> 分 6 个阶段（P0-P5），每阶段交付一个可运行的增量版本。

---

## P0：骨架 + 第一次 API Call ✅ 已完成

**目标**：仓库搭好，能调通一次 LLM API。

**交付物**：
- ✅ 仓库结构（src/ming/）+ pyproject.toml + 基础依赖
- ✅ LiteLLM 接入层：统一接口调通 DeepSeek API
- ✅ 最简 CLI：`python -m ming "你好"` → 得到回复
- ✅ 配置系统：provider/model/api_key 从 config 读取（YAML 三层叠加）
- ⬜ 基础日志（推迟到 P1）

**已做的决策**：
- 目录结构：src layout（src/ming/下 core/tools/context/memory 四模块）
- Config 格式：YAML（default.yaml + local.yaml + 环境变量三层）
- LLM 接入：LiteLLM（不直接用 OpenAI SDK——LiteLLM 统一接口打通所有 provider）

---

## P1：α_LOOP 核心循环

**目标**：一个能用的单 agent，类似最简 Claude Code——能推理、能调工具、能循环。

**交付物**：
- Agentic loop 实现：推理 → tool call → 执行 → 结果回喂 → 继续推理 → 直到 stop
- 工具系统骨架：至少 bash_exec、file_read、file_write 三个内建工具
- System prompt 管理：基座层（固定 prompt）
- 对话历史管理：消息列表 + token 计数
- 基础 T1 CoVe：输出前追加一次"检查自己"的 prompt（最简实现）

**关键决策**：
- Tool 定义格式（MCP schema vs 自定义）
- 消息数据模型（Pydantic models）
- Tool 执行的沙盒策略

**验证**：能让 Ming 完成一个多步编码任务（如"创建一个 Python 文件并运行测试"）

---

## P2：Context 四层 + 基础记忆

**目标**：Context 工程落地。有 system prompt 分层、会话管理、基础记忆。

**交付物**：
- 四层 Context 组装器：基座层 / 会话层 / 对话层 / 即时层 分开管理
- 会话层：项目上下文加载（MING.md 等效）+ 用户记忆加载
- 对话层：token 计数 + Compaction 触发器（双阈值 50%/85%）
- Compaction 实现：tool pruning 前置 + 结构化摘要（Goal/Progress/Decisions）
- 记忆文件系统：MEMORY.md + 基础编码（会话结束时提取关键信息写入）
- Prefix cache 友好排列验证

**关键决策**：
- 记忆文件存储格式
- Compaction prompt 模板
- Token 计数库选择（tiktoken? litellm 内建?）

**验证**：长对话（50+ 轮）不崩、compaction 后信息保留质量可接受

---

## P3：Gate + Automaticity

**目标**：守门人落地，Ming 开始有"判断该不该启动多 Agent"的能力。行为模式库初具形态。

**交付物**：
- 守门人 Gate 实现：7 条规则评估器
  - 规则 1-3：静态规则（关键词/文件数/模式匹配）
  - 规则 4：token 计数判定
  - 规则 5：用户显式指令解析
  - 规则 6：经验库查询
  - 规则 7：Automaticity 值查询
- Automaticity 数据模型：行为模式 → Automaticity 值映射
- 行为模式库：CRUD + 模式匹配（当前任务匹配哪个行为模式）
- Tier 信号反馈：任务完成后更新 Automaticity 值
- T2 偏差清单：system prompt 硬编码实现
- T3 事实核验子：fresh-context sub-agent 调用（独立 LLM call）

**关键决策**：
- 行为模式的存储格式（JSON? SQLite?）
- 模式匹配算法（关键词? embedding?——E6 说 No embeddings）
- Gate 规则的阈值初始化

**验证**：
- 简单任务走单核，复杂任务命中规则升对抗档（此阶段升档只打日志，不真跑 β）
- Automaticity 值随反复成功上升、失败下降

---

## P4：对抗系统（β Fork + γ 收敛）

**目标**：Ming 核心差异化能力——双 Agent 对抗 + 收敛。

**交付物**：
- Fork 机制：从当前对话状态分叉出两个独立 session（共享 prefix，不同注入 prompt）
- β Agent：独立分析 prompt + T4 硬约束（不提 α、不说审查、用"独立分析"）
- α/β 并行执行：两个 LLM call 并行（asyncio）
- 强制结构化输出：方案/关键假设/放弃的替代/置信度
- γ 阶段1 收敛器：fresh-context 比较器
- γ 阶段2 分歧厘清：Fork 继承历史 + 诊断 prompt
- 输出合并：一致→正常输出；分歧→后果摘要；对立→人类裁决
- Tier 信号回流：T4/T6/T7 结果更新 Automaticity
- Cache 利用验证：确认 β 的 prefix cache hit 生效

**关键决策**：
- Fork 的工程实现（deepcopy session state? 序列化/反序列化?）
- 并行执行框架（asyncio? threading?）
- 结构化输出的解析（JSON mode? prompt 约束?）
- γ 的 fresh-context 如何构建

**验证**：
- 对抗档端到端运行：Gate 升档 → Fork → α/β 并行 → γ 收敛 → 输出
- 成本验证：长 context 场景实测 ≤ 1.3x
- T4 硬约束验证：β 输出不含 α 相关词汇

---

## P5：Error Recovery + 生产化

**目标**：加入错误恢复、循环检测、回滚，达到可日常使用的稳定性。

**交付物**：
- L1 工具级：harness 自动重试（指数退避）+ 错误回喂 LLM
- L4 系统级：provider fallback（turn-scoped）
- L5 循环检测：
  - 指纹层：tool call hash 去重（3 警告 / 5 阻断）
  - 天花板层：迭代上限 + 成本预算 + 墙钟超时（可配置）
  - 人类兜底：天花板触发时暂停
- 回滚：Git auto-commit + 回滚命令
- bash 预检：`bash -n` 语法检查
- Compaction 健壮性：失败后不无限重试
- 基础 Dreaming：会话结束时触发 Light 巩固
- CLI 完善：交互式对话 + 命令（/compact, /rewind, /status）

**关键决策**：
- 循环检测阈值的合理值
- Git auto-commit 的粒度（每次编辑? 每轮?）
- 天花板的默认值

**验证**：
- 注入故障场景（API 超时、工具失败、模型循环）验证恢复
- 长时间使用（1小时+）的稳定性
- 错误恢复不丢失用户进展

---

## 时间线估算

| 阶段 | 预估工期 | 前置依赖 | 里程碑 |
|------|---------|---------|--------|
| P0 | 1-2 天 | 无 | `ming "你好"` 得到回复 |
| P1 | 3-5 天 | P0 | 完成多步编码任务 |
| P2 | 3-5 天 | P1 | 长对话不崩 + compaction 工作 |
| P3 | 5-7 天 | P2 | Gate 评估 + Automaticity 升降 |
| P4 | 7-10 天 | P3 | 对抗档端到端运行 |
| P5 | 5-7 天 | P4 | 日常可用稳定性 |

**总计**：约 4-6 周达到可用状态。

---

## 技术栈确认

```
语言:       Python 3.12+
LLM 接入:   LiteLLM（多 provider 统一接口）
工具协议:   MCP（Model Context Protocol）
主力模型:   DeepSeek-V4（开发）→ 多模型测试（生产）
数据模型:   Pydantic v2
异步:       asyncio（α/β 并行）
存储:       文件系统（记忆/行为模式）+ SQLite（经验库/检索）
CLI:        rich + prompt_toolkit
版本控制:   Git（代码 + 用户项目回滚）
测试:       pytest + 集成测试场景
```

---

## 风险与注意事项

| 风险 | 影响 | 缓解 |
|------|------|------|
| DeepSeek 结构化输出能力不足 | P4 α/β 输出格式不稳定 | 用 prompt 约束 + 解析容错；必要时测试其他模型 |
| Fork cache 命中率不如预期 | 对抗档成本 >> 1.2x | P4 阶段实测，不命中则调整 prefix 排列 |
| 循环检测误报 | 正常长任务被终止 | 天花板阈值可配置 + 人类可选择继续 |
| Compaction 信息损失 | 长对话质量退化 | 结构化模板 + 迭代更新 + 实测调优 |
| 开源模型幻觉率高 | T1/T3 负担重 | 能力地板设计，T0-T3 永远全量开 |
