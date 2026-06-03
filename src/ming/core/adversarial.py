"""Adversarial collaboration system — α/β Fork + γ convergence.

Implements P3 System 2 collaboration:
  1. Fork conversation into two independent sessions (α and β)
  2. α and β reason independently in parallel
  3. γ Phase 1: compare outputs (fresh context)
  4. γ Phase 2: divergence resolution (only if deadlocked)
  5. Present unified output to user
"""

import asyncio
import logging

from ming.config import LLMConfig
from ming.core.llm import Message, call_llm

logger = logging.getLogger("ming")

# T4 hard constraints: β must NOT know about α
ALPHA_INJECTION = """\
你是决策分析者。基于以上讨论，给出你认为最优的方案。
要求：
1. 用辩证法：先给方案，自己找反面论据，说明如何应对
2. 列出关键假设——哪些条件不成立，方案就失败
3. 列出放弃的替代方案及理由
4. 给出置信度（高/中/低）及理由

输出格式（严格遵守）：
## 方案
## 辩证分析
## 关键假设
## 放弃的替代方案
## 置信度"""

BETA_INJECTION = """\
你是独立分析者。基于以上讨论，独立思考这个问题。
关键规则：
- 你不是在帮别人完善方案——你是在独立分析
- 不要假设已有的讨论方向一定是对的
- 你的价值在于发现别人没想到的问题

要求：
1. 给出你自己对问题的理解（可以和之前讨论不同）
2. 列出最容易被忽视的风险（至少3个），每个附具体失败场景
3. 如果你认为整个方向有问题，直说

输出格式（严格遵守）：
## 我对问题的理解
## 被忽视的风险
## 整体评估"""

GAMMA_COMPARE_PROMPT = """\
你是一个内部质量控制模块。以下是对同一问题的两份独立分析（视角A和视角B），请比较并合成。

视角A：
{alpha_output}

视角B：
{beta_output}

## 你的任务

1. 判定一致性：CONSISTENT（一致/细节差异）、COEXIST（方向不同可共存）、OPPOSED（根本对立）

2. 按一致性等级，撰写**给用户的最终回答**：
   - CONSISTENT：直接输出合并后的结论，语气自然。
     不要提及"两个视角""多角度分析"等内部细节。
   - COEXIST：说"这个问题有几种不同思路"，列出选项供用户选择。不要说"经过多角度分析"。
   - OPPOSED：说"这个问题有一个关键分歧需要你来判断"，列出分歧的两面。

重要：用户不知道有多个分析视角存在。给用户的回答必须读起来像一个助手自然给出的回复，不能出现"分析者A""视角A/B""两位分析者""独立分析"等内部术语。

输出格式：
## 一致性等级
(CONSISTENT / COEXIST / OPPOSED)
## 给用户的回答"""

GAMMA_RESOLVE_PROMPT = """\
你是一个内部质量控制模块。对同一问题的两份独立分析存在根本对立，请诊断分歧根源并整理给用户。

视角A：
{alpha_output}

视角B：
{beta_output}

你的任务：
1. 这两个视角的核心分歧在哪？
2. 它们各自的盲点是什么？
3. 有没有第三种思路？

然后撰写**给用户的回答**——用户不知道内部有多个分析，回答要像一个助手经过深入思考后的自然表达：
- 说"这个问题我想了比较久，核心分歧在于..."
- 列出需要用户判断的关键抉择点
- 给出你的倾向性建议

不能出现"分析者""视角A/B""两份分析"等内部术语。

输出格式：
## 分歧诊断（内部记录，不给用户看）
## 给用户的回答"""


class AdversarialResult:
    """Result of adversarial collaboration."""

    def __init__(
        self,
        final_output: str,
        consistency: str,  # "CONSISTENT", "COEXIST", "OPPOSED"
        alpha_output: str = "",
        beta_output: str = "",
        gamma_output: str = "",
        tier_signal: str = "",
    ):
        self.final_output = final_output
        self.consistency = consistency
        self.alpha_output = alpha_output
        self.beta_output = beta_output
        self.gamma_output = gamma_output
        self.tier_signal = tier_signal


async def _run_agent(
    messages: list[Message],
    injection: str,
    config: LLMConfig,
) -> str:
    """Run a single agent (α or β) with injected prompt."""
    forked = list(messages)  # shallow copy — shared prefix
    forked.append(Message(role="user", content=injection))

    response = await call_llm(messages=forked, config=config)
    return response.content


async def run_adversarial(
    dialog_history: list[Message],
    config: LLMConfig,
) -> AdversarialResult:
    """Execute full adversarial collaboration pipeline.

    Args:
        dialog_history: Current conversation (base + session + dialog layers).
        config: LLM configuration.

    Returns:
        AdversarialResult with merged output and metadata.
    """
    logger.info("Adversarial: starting α/β Fork")

    # Step 1: Fork — α and β run in parallel with same prefix, different injections
    alpha_task = _run_agent(dialog_history, ALPHA_INJECTION, config)
    beta_task = _run_agent(dialog_history, BETA_INJECTION, config)

    alpha_output, beta_output = await asyncio.gather(alpha_task, beta_task)

    logger.info(f"Adversarial: α={len(alpha_output)} chars, β={len(beta_output)} chars")

    # Step 2: γ Phase 1 — compare (fresh context, only sees outputs)
    gamma_compare_messages = [
        Message(
            role="system",
            content=(
                "你是内部质量控制模块。你的输出中'给用户的回答'部分会直接展示给用户，"
                "必须自然流畅，不能暴露内部多视角分析的存在。"
            ),
        ),
        Message(
            role="user",
            content=GAMMA_COMPARE_PROMPT.format(
                alpha_output=alpha_output[:3000],
                beta_output=beta_output[:3000],
            ),
        ),
    ]

    gamma_response = await call_llm(messages=gamma_compare_messages, config=config)
    gamma_output = gamma_response.content

    # Determine consistency level
    consistency = "CONSISTENT"
    gamma_lower = gamma_output.lower()
    if "opposed" in gamma_lower or "根本对立" in gamma_lower:
        consistency = "OPPOSED"
    elif "coexist" in gamma_lower or "可共存" in gamma_lower or "方向不同" in gamma_lower:
        consistency = "COEXIST"

    logger.info(f"Adversarial: γ Phase 1 → {consistency}")

    # Step 3: Handle based on consistency
    if consistency == "CONSISTENT":
        # α/β agree — merge and present as normal response (don't expose architecture)
        tier_signal = "T4_agree"
        final_output = _extract_user_output(gamma_output)

    elif consistency == "COEXIST":
        # Different but compatible — present options (no architecture exposure)
        tier_signal = "T4_insight"
        final_output = _extract_user_output(gamma_output)

    else:  # OPPOSED
        # Step 3b: γ Phase 2 — divergence resolution
        logger.info("Adversarial: γ Phase 2 — divergence resolution")

        gamma_resolve_messages = list(dialog_history) + [
            Message(
                role="user",
                content=GAMMA_RESOLVE_PROMPT.format(
                    alpha_output=alpha_output[:3000],
                    beta_output=beta_output[:3000],
                ),
            ),
        ]

        resolve_response = await call_llm(messages=gamma_resolve_messages, config=config)

        tier_signal = "T6_clarified"
        final_output = _extract_user_output(resolve_response.content)

    return AdversarialResult(
        final_output=final_output,
        consistency=consistency,
        alpha_output=alpha_output,
        beta_output=beta_output,
        gamma_output=gamma_output,
        tier_signal=tier_signal,
    )


def _extract_user_output(gamma_output: str) -> str:
    """Extract the user-facing portion from γ's output.

    Strips internal analysis sections, returns only what the user should see.
    """
    for marker in ["## 给用户的回答", "## 给用户的输出"]:
        if marker in gamma_output:
            return gamma_output.split(marker, 1)[1].strip()
    # If no marker found, strip any internal sections
    for internal_marker in ["## 一致性等级", "## 分歧诊断"]:
        if internal_marker in gamma_output:
            parts = gamma_output.split(internal_marker)
            # Return everything after the last internal section
            remaining = parts[-1]
            for m in ["## 给用户的回答", "## 给用户的输出"]:
                if m in remaining:
                    return remaining.split(m, 1)[1].strip()
    return gamma_output
