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
from typing import Any

from ming.config import LLMConfig
from ming.core.llm import LLMResponse, Message, call_llm

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
两个独立分析者对同一个问题给出了不同的分析。请逐字段比较，判定一致性等级。

分析者A的输出：
{alpha_output}

分析者B的输出：
{beta_output}

你的任务：
1. 判定一致性等级：CONSISTENT（一致/细节差异）、COEXIST（方向不同可共存）、OPPOSED（根本对立）
2. 如果 CONSISTENT：给出合并后的结论
3. 如果 COEXIST：列出双方差异，翻译为用户可选的选项
4. 如果 OPPOSED：列出对立的核心分歧点

输出格式：
## 一致性等级
(CONSISTENT / COEXIST / OPPOSED)
## 分析
## 给用户的输出"""

GAMMA_RESOLVE_PROMPT = """\
两个分析者对同一个问题存在根本对立。诊断对立的根源。

分析者A的输出：
{alpha_output}

分析者B的输出：
{beta_output}

跳出他们的框架：
1. A 和 B 共同遗漏了什么维度？
2. 问题本身的定义是否有偏？
3. 是否存在第三种框架能解释双方分歧？
4. 给决策者一句话建议

输出格式：
## 共同盲点
## 分歧根源诊断
## 第三视角
## 给决策者的建议"""


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
        Message(role="system", content="你是收敛分析者，负责比较两个独立分析的一致性。"),
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
        # Different but compatible — present options
        tier_signal = "T4_insight"
        final_output = (
            "经过多角度分析，有以下不同视角供你选择：\n\n"
            + _extract_user_output(gamma_output)
        )

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
        final_output = (
            "分析过程中出现了根本性分歧，以下是厘清后的分歧点，需要你来裁决：\n\n"
            + resolve_response.content
        )

    return AdversarialResult(
        final_output=final_output,
        consistency=consistency,
        alpha_output=alpha_output,
        beta_output=beta_output,
        gamma_output=gamma_output,
        tier_signal=tier_signal,
    )


def _extract_user_output(gamma_output: str) -> str:
    """Extract the user-facing portion from γ's output."""
    marker = "## 给用户的输出"
    if marker in gamma_output:
        return gamma_output.split(marker, 1)[1].strip()
    return gamma_output
