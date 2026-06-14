from pathlib import Path

from ming.eval.cost import summarize_trace_budget
from ming.eval.fingerprint import behavior_fingerprint
from ming.eval.golden import load_golden_conversation
from ming.eval.judges import select_judges_for_turn


def test_select_judges_uses_trace_shape_not_fixed_full_panel():
    single_tool_turn = {
        "execution": "single",
        "single_agent": {
            "steps": [
                {
                    "tool_calls": [
                        {"name": "file_write", "result_is_error": False},
                    ]
                }
            ]
        },
    }
    adversarial_turn = {"execution": "adversarial", "adversarial": {"gamma_phase2_ran": True}}
    compacted_turn = {"compaction_events": [{"trigger": "threshold"}]}

    assert select_judges_for_turn(single_tool_turn) == ["gate_judge", "tool_use_judge"]
    assert select_judges_for_turn(adversarial_turn) == [
        "gate_judge",
        "gamma_output_judge",
        "adversarial_value_judge",
    ]
    assert "compaction_judge" in select_judges_for_turn(compacted_turn)


def test_golden_conversation_loader_normalizes_scenario_file(tmp_path):
    path = tmp_path / "pomodoro.yaml"
    path.write_text(
        """
id: pomodoro-page
description: 创建并运行番茄钟页面
tags: [ui, tools]
turns:
  - user: 帮我写一个番茄钟页面
    expect:
      files:
        - pomodoro.html
      final_contains:
        - 番茄钟
""",
        encoding="utf-8",
    )

    golden = load_golden_conversation(path)

    assert golden.id == "pomodoro-page"
    assert golden.turns[0].user == "帮我写一个番茄钟页面"
    assert golden.turns[0].expect["files"] == ["pomodoro.html"]
    assert golden.source_path == Path(path)


def test_behavior_fingerprint_is_stable_and_sensitive_to_core_behavior():
    trace = {
        "turns": [
            {
                "execution": "single",
                "gate": {"mode": "single", "triggered_rules": []},
                "single_agent": {
                    "steps": [
                        {"tool_calls": [{"name": "file_write", "loop_status": "ok"}]},
                        {"tool_calls": []},
                    ],
                    "l5_ceiling_hit": None,
                },
                "feedback": {"tier_signal": "T3_pass"},
            }
        ]
    }

    first = behavior_fingerprint(trace)
    second = behavior_fingerprint(trace)
    trace["turns"][0]["single_agent"]["steps"][0]["tool_calls"][0]["name"] = "bash"
    changed = behavior_fingerprint(trace)

    assert first == second
    assert first != changed
    assert first.startswith("bf_")


def test_cost_budget_summary_flags_over_budget_trace():
    trace = {
        "session_metrics": {
            "total_llm_calls": 8,
            "total_prompt_tokens": 12000,
            "total_completion_tokens": 4000,
            "total_cost_usd": 0.25,
        }
    }

    summary = summarize_trace_budget(trace, max_cost_usd=0.1, max_llm_calls=10)

    assert summary["total_cost_usd"] == 0.25
    assert summary["over_budget"] is True
    assert summary["reasons"] == ["cost"]
