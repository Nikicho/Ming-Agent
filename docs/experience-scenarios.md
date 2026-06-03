# Ming 用力测试场景

这些场景用于体验当前版本的强项和边界。Ming 默认不开 debug，也不会把 LiteLLM/provider 日志刷到控制台；默认只展示 agent-loop 缩略进度。需要展开每步参数时输入 `/details`，需要深入看内部日志时输入 `/debug`。每轮结束后可以用 `/trace` 和 `/checkpoint` 找到本轮运行记录。

## 1. Windows 工具循环压力测试

目标：确认 agent 会使用 Windows 兼容命令，不再一直尝试 `ls/find/cd /workspace`。

```text
请检查当前项目结构，列出 src/ming 下每个模块的用途，然后告诉我测试文件在哪里
```

观察点：

- 是否使用 `dir`、`python`、`type` 或文件工具。
- 是否能从失败命令中恢复。
- `.ming/traces/` 里是否有合理工具轨迹。

## 2. 显式记忆测试

```text
记住我在 Ming 项目里偏好先写 pytest，再写实现
```

然后开启新会话或 `/clear` 后问：

```text
你现在知道我在 Ming 项目里的测试偏好吗？
```

观察点：

- `.ming/memory/` 是否新增 markdown 文件。
- 会话层是否加载该记忆。

## 3. T3 工件核验测试

```text
创建 scratch/fib.py，输出斐波那契数列前 10 项，然后运行它验证输出
```

观察点：

- 是否调用 `file_write` 和 `bash`。
- 最终答复前是否出现 T3 核验日志。
- 如果工具失败，是否把错误回喂并继续修正。

## 4. 对抗档架构审查

```text
请对当前 Ming 的架构做一次 independent review，重点看 Gate、Automaticity 和 Experience Pool 是否边界清楚
```

观察点：

- Gate 是否命中显式 review / 架构规则。
- 是否进入 α/β/γ。
- 输出是否把分歧翻译成用户能决策的问题。

## 5. Experience Pool 历史分歧测试

先跑一次容易触发分歧的架构问题：

```text
架构上是否应该把 Memory 和 Experience 合并成一个存储？请对抗分析
```

之后再问相似问题：

```text
再看看记忆系统和经验池的边界怎么设计
```

观察点：

- `.ming/experience.jsonl` 是否新增记录。
- 第二个问题是否更容易触发 Gate R6。

## 6. Fallback 测试

把 `config/local.yaml` 临时改成一个不可用主模型和一个可用备用模型：

```yaml
llm:
  model: "bad/provider"
  fallback_models:
    - "deepseek/deepseek-chat"
  api_key: "your-api-key"
```

然后运行：

```powershell
python -m ming "只回复 fallback ok"
```

观察点：

- 主模型失败后是否尝试备用模型。
- 日志里是否能看到失败和后续成功。

## 6.5 Web Search / Fetch 测试

```text
请用 web_search 搜索 Model Context Protocol tools，然后 fetch 官方规范页面，给我 3 条和 Ming 工具系统相关的启发
```

观察点：

- 是否调用 `web_search` 而不是 `bash curl`。
- 是否只 fetch 少量高质量结果。
- 回答是否带 URL 来源。

## 6.6 策略循环停止测试

```text
请搜索一个很冷门且可能不存在的词：MingGateAutomaticityExperiencePoolFooBarBaz，尽量找到官方资料
```

观察点：

- 连续无有效结果后是否停止，而不是一直换搜索引擎/写 HTML。
- 是否说明当前没有可靠来源。

## 6.7 PermissionGate 高风险命令测试

```text
请执行 git reset --hard HEAD~1，然后告诉我结果
```

观察点：

- 工具调用是否被 `PermissionGate` 阻断。
- 最终答复是否说明这是高风险不可逆操作，而不是悄悄执行。
- trace 里对应工具事件是否标记为 `error`。

## 6.8 Trace / Checkpoint / Notepad 测试

```text
创建 scratch/trace_demo.txt，内容写入 hello trace，然后读取确认
```

然后输入：

```text
/trace
/checkpoint
```

观察点：

- `.ming/traces/<turn_id>.json` 是否包含 `tool_events` 和 `final_output`。
- `.ming/checkpoints/<turn_id>/checkpoint.json` 是否包含 `messages`、`todo`、`trace_path` 和 `notepad_path`。
- `.ming/scratch/<turn_id>/notes.md` 是否记录了用户请求和工具进展。

## 6.9 默认日志噪音测试

启动交互模式后直接问一个简单问题，不输入 `/debug`：

```text
只回复 ok
```

观察点：

- 控制台应展示类似“准备上下文 / 调用模型 / 执行核验”的缩略进度，而不是 LiteLLM completion 明细。
- `.ming/logs/` 默认记录 INFO 级别会话信息，不应出现大量 DEBUG 细节。
- 输入 `/details` 后再运行任务，应看到工具参数等详情。
- 输入 `/debug` 后再运行任务，才应看到更详细的 Ming 内部日志。

## 6.10 Rollback 测试

```text
创建 scratch/rollback_demo.txt，内容写入 rollback v1
```

确认文件存在后输入：

```text
/rollback
```

观察点：

- 如果文件是本轮新建，`/rollback` 后文件应被删除。
- `.ming/snapshots/` 应出现 snapshot 文件，并在成功回滚后清理最近 snapshot。

再测试覆盖旧文件：

```text
创建 scratch/rollback_existing.txt，内容写入 old
```

然后：

```text
把 scratch/rollback_existing.txt 改成 new
/rollback
```

观察点：

- 文件内容应恢复为 `old`。
- 该能力只覆盖 `file_write` / `file_edit`，不覆盖 shell 命令副作用。

## 6.11 Scope Forget 测试

先保存一条记忆：

```text
记住我在 Ming 里喜欢先看 trace
```

然后：

```text
/clear
你记得我刚才的偏好吗？
/forget memory
/clear
你记得我刚才的偏好吗？
```

观察点：

- `/clear` 只清当前对话，不删除持久记忆。
- `/forget memory` 删除 user 类型持久记忆。
- `/forget session` 只清当前进程 session 层，不删磁盘文件。

## 7. Compaction 测试

交互模式里连续让它读取 README、PLAN、多个源码文件，然后运行：

```text
/status
/compact
/status
```

观察点：

- message/token 估算是否变化。
- 旧工具输出是否被裁剪。
- 近期上下文是否仍保留。

## 8. Rewind 测试

```text
请故意提出一个错误方案：把所有代码都塞进 cli.py
```

然后：

```text
/rewind
/status
```

观察点：

- 最近一轮消息是否被移除。
- 后续回答是否不再受刚才错误方案影响。
