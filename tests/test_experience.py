from ming.memory.experience import ExperienceStore


def test_experience_store_reports_historical_divergence_for_similar_failed_task(tmp_path):
    store = ExperienceStore(tmp_path / "experience.jsonl")

    store.record("设计架构方案", "T4_insight", "adversarial")

    assert store.has_historical_divergence("帮我看下架构设计")
    assert not store.has_historical_divergence("列出目录")
