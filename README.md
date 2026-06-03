# Ming (明)

> 知常曰明，不知常，妄作凶 —— 《道德经》

An open-source, model-agnostic AI agent that augments human System 2 thinking.

Ming is not just another chatbot wrapper — it implements biomorphic cognitive architecture with adversarial collaboration, automaticity learning, and multi-layer context management.

## Quick Start

### 1. Install

```bash
cd D:\Ming
python -m venv .venv
.venv\Scripts\activate       # Windows
# source .venv/bin/activate  # Linux/Mac
pip install -e .
```

### 2. Configure

Create `config/local.yaml` (gitignored, safe for secrets):

```yaml
llm:
  model: "deepseek/deepseek-v4-flash"   # or deepseek/deepseek-v4-pro, openai/gpt-4o, etc.
  api_key: "your-api-key-here"
```

Or use environment variable:

```bash
export MING_LLM_API_KEY="your-key"
export MING_LLM_MODEL="deepseek/deepseek-v4-flash"
```

### 3. Run

```bash
# Single question
python -m ming "帮我写一个 hello world"

# Interactive mode
python -m ming
```

## Features

### 🔧 Tool Use (Agentic Loop)

Ming can use tools to complete tasks, not just talk about them:

- **bash** — Execute shell commands
- **file_read** — Read files with line numbers
- **file_write** — Create or overwrite files
- **file_edit** — Surgical string replacement in files

Ming loops automatically: reason → call tool → read result → reason again → until done.

```
You: 创建一个 Python 文件算斐波那契数列前10个，然后运行它
Ming: [calls file_write to create fib.py]
      [calls bash to run python fib.py]
      完成！输出：0 1 1 2 3 5 8 13 21 34
```

### 🛡️ 守门人 Gate (Adversarial Routing)

Every task is evaluated by 7 rules. If any fires, Ming upgrades from single-agent to adversarial mode:

| Rule | Trigger |
|------|---------|
| R1 | Irreversible operations (delete, drop, force push) |
| R2 | Architectural changes (schema, core principles) |
| R3 | Cross-module impact (≥5 files) |
| R4 | Rich context (≥30K tokens, Fork cache pays off) |
| R5 | User explicitly requests review |
| R6 | Historical divergence on similar tasks |
| R7 | Low Automaticity (unfamiliar task type) |

### ⚔️ Adversarial Collaboration (α/β/γ)

When Gate triggers adversarial mode:

1. **Fork** — Two agents (α and β) independently analyze the same problem
2. **α** sees "决策分析者" prompt, uses dialectical reasoning
3. **β** sees "独立分析" prompt — **does NOT know α exists** (T4 hard constraint)
4. **γ Phase 1** — Fresh-context comparison of α/β outputs
5. **γ Phase 2** — Divergence resolution (only if fundamentally opposed)

Results:
- **CONSISTENT** → Merged output, user sees a normal response (architecture hidden)
- **COEXIST** → Options presented for user to choose
- **OPPOSED** → Divergence diagnosed, user makes the call

### 📊 Automaticity (Learning from Experience)

Ming tracks how "automatic" each task type should be:

- **High automaticity** (0.8+): Direct execution, minimal verification
- **Medium** (0.3-0.8): Standard reasoning with checks
- **Low** (<0.3): Full deliberation, may trigger adversarial mode

Automaticity updates based on outcomes:
- β independently agrees with α → ↑↑↑ big increase
- β finds blind spot α missed → ↓↓↓ big decrease
- Human rejects output → ↓↓↓↓ maximum decrease

Stored in `.ming/automaticity.json`, persists across sessions.

### 📝 Context Management (Four-Layer Model)

Context window organized for cache efficiency:

| Layer | Content | Stability |
|-------|---------|-----------|
| **Base** | System prompt, T2 bias checklist | Never changes |
| **Session** | Memories, behavior patterns | Stable within session |
| **Dialog** | Conversation history, tool outputs | Grows per turn |
| **Instant** | Current input, Gate/Fork injections | Fresh each turn |

**Auto-compaction**: When context exceeds 50%, old tool outputs are pruned (no LLM cost), then old dialog is summarized. Safety net at 85% prevents overflow.

### 🧠 Memory System

File-based persistent memory in `.ming/memory/`:

```bash
You: 记住我喜欢用 pytest 而不是 unittest
Ming: [saves to .ming/memory/]
# Next session, Ming loads this preference automatically
```

### 🔄 Error Recovery

**Five-level error handling**:

| Level | What | Recovery |
|-------|------|----------|
| L1 | Tool failure | Auto-retry → LLM reasons about fix |
| L2 | Reasoning error | Re-enter loop → escalate to adversarial |
| L3 | β/γ crash | Degrade to single-agent |
| L4 | API down | Retry → fallback provider |
| L5 | Loop/repetition | Fingerprint detection → ceiling → human |

**Loop detection**: SHA-256 fingerprinting of tool calls. 3 identical calls → warning, 5 → blocked.

**Ceiling**: Configurable iteration limit (default 50) + wall-clock timeout (default 300s).

## CLI Commands

| Command | Action |
|---------|--------|
| `/quit` | Exit |
| `/clear` | Clear conversation, start fresh |
| `/status` | Show token usage, message count, patterns |
| `/debug` | Toggle debug logging (see Gate decisions, tool calls, etc.) |

## Configuration

All settings in `config/default.yaml`, override in `config/local.yaml`:

```yaml
llm:
  model: "deepseek/deepseek-v4-flash"
  api_key: ""
  temperature: 0.3
  max_tokens: 4096

context:
  max_context_tokens: 128000
  compaction_threshold: 0.50        # primary compaction at 50%
  compaction_safety_threshold: 0.85 # safety net at 85%

agent:
  max_iterations: 50    # L5 ceiling per turn
  max_seconds: 300      # wall-clock timeout
  max_cost_per_turn: 0  # 0 = unlimited
```

### Supported Models (via LiteLLM)

```yaml
# DeepSeek
model: "deepseek/deepseek-v4-flash"
model: "deepseek/deepseek-v4-pro"

# OpenAI
model: "openai/gpt-4o"
model: "openai/o4-mini"

# Anthropic
model: "anthropic/claude-sonnet-4-20250514"

# 300+ more via LiteLLM...
```

## Architecture

Ming implements a biomorphic cognitive architecture inspired by brain mechanisms:

```
User Input
  → Context Assembly (thalamus: four-layer model)
  → Gate Evaluation (ACC: 7 trigger rules)
  → Single Mode: α_LOOP (PFC: reason + tools + T0-T3 metacognition)
     OR
  → Adversarial Mode: Fork α/β → γ Convergence (P3: institutional opposition)
  → Output (LLM natural formatting + γ merge)
  → Feedback (Automaticity update + memory encoding)
```

For detailed design docs, see the [design document](../Obsidian%20Vault/自主进化Agent研究/Ming%20架构设计文档.md).

## Project Structure

```
src/ming/
├── cli.py                   # Interactive CLI
├── config.py                # Three-layer config system
├── core/
│   ├── agent.py             # Main agent: orchestrates all subsystems
│   ├── llm.py               # LiteLLM unified interface
│   ├── gate.py              # 守门人 (7 trigger rules)
│   ├── automaticity.py      # Behavior patterns + learning
│   ├── adversarial.py       # α/β Fork + γ convergence
│   └── loop_detection.py    # SHA-256 fingerprint loop detection
├── context/
│   └── manager.py           # Four-layer context + compaction
├── memory/
│   └── store.py             # File-based persistent memory
└── tools/
    ├── base.py              # Tool base class + registry
    ├── bash.py              # Shell execution
    └── file.py              # File read/write/edit
```

## License

MIT

## Credits

Designed with insights from Claude Code, Codex, Clowder-AI, and OpenClaw.

Named after 《道德经》: 知常曰明 — "To understand the eternal is to be enlightened."
