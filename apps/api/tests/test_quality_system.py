from __future__ import annotations

import pytest


def test_quality_signals_drive_chunk_actions_without_stopword_gate():
    from app.services.quality.policies import ChunkQualityPolicy
    from app.services.quality.signals import build_quality_signals

    toc = "\n".join(["1", "2", "3", "4", "5", "6", "7", "8"])
    toc_decision = ChunkQualityPolicy().decide(build_quality_signals(target_type="chunk", text=toc, content_kind="pdf_page"))
    assert toc_decision.action == "summary_only"
    assert "toc_layout_noise" in toc_decision.reasons
    assert toc_decision.audit["retention_decision"]["retain"] is True
    assert toc_decision.audit["route_eligibility"]["graph_extraction"] is False

    definition = (
        "Residual Network is defined as the remaining capacity graph used by augmenting path algorithms. "
        "It explains how maximum flow updates admissible edges."
    )
    graph_decision = ChunkQualityPolicy().decide(build_quality_signals(target_type="chunk", text=definition, content_kind="markdown"))
    assert graph_decision.action == "graph_candidate"
    assert graph_decision.audit["signals"]["semantic_density"]["definition_score"] == 1.0
    assert graph_decision.audit["route_eligibility"]["graph_extraction"] is True


def test_chunk_policy_only_physically_discards_mechanical_noise():
    from app.services.quality.policies import ChunkQualityPolicy
    from app.services.quality.signals import build_quality_signals

    policy = ChunkQualityPolicy()
    short = policy.decide(build_quality_signals(target_type="chunk", text="自环", content_kind="markdown"))
    assert short.action == "evidence_only"
    assert short.audit["retention_decision"]["retain"] is True

    formula = policy.decide(build_quality_signals(target_type="chunk", text="Q = 1 / 2m", content_kind="formula"))
    assert formula.action == "retrieval_candidate"
    assert formula.audit["route_eligibility"]["retrieval"] is True
    assert formula.audit["route_eligibility"]["graph_extraction"] is False

    output = policy.decide(build_quality_signals(target_type="chunk", text="[Output]\n1\n2\n3", content_kind="output"))
    assert output.action == "evidence_only"
    assert output.audit["route_eligibility"]["graph_extraction"] is False

    empty = policy.decide(build_quality_signals(target_type="chunk", text="", content_kind="markdown"))
    assert empty.action == "discard"
    assert empty.audit["retention_decision"]["retain"] is False


def test_concept_policy_rejects_structural_generic_noise_and_accepts_evidence():
    from app.services.quality.policies import ConceptQualityPolicy
    from app.services.quality.signals import build_quality_signals

    policy = ConceptQualityPolicy()
    structural = policy.decide(build_quality_signals(target_type="concept", text="Week 2 Solutions"), evidence_count=2, specificity_score=0.6)
    assert structural.action == "reject"
    assert "structural_container" in structural.reasons

    generic = policy.decide(build_quality_signals(target_type="concept", text="Algorithm"), evidence_count=1, specificity_score=0.2)
    assert generic.action == "reject"
    assert "generic_low_specificity" in generic.reasons

    accepted = policy.decide(build_quality_signals(target_type="concept", text="Residual Network"), evidence_count=2, specificity_score=0.72)
    assert accepted.action == "accept"
    assert accepted.reasons == ["policy_passed"]


def test_concept_policy_keeps_short_domain_terms_for_later_evidence_gate():
    from app.services.concept_graph import concept_quality, is_stageable_concept
    from app.services.quality.policies import ConceptQualityPolicy
    from app.services.quality.signals import build_quality_signals

    assert concept_quality("自环")["valid"] is True
    assert concept_quality("团")["valid"] is True
    assert concept_quality("ΔQ")["valid"] is True

    policy = ConceptQualityPolicy()
    assert policy.decide(build_quality_signals(target_type="concept", text="Unit Traversal Time"), evidence_count=2, specificity_score=0.7).action == "accept"
    assert policy.decide(build_quality_signals(target_type="concept", text="Community assignment r"), evidence_count=2, specificity_score=0.7).action == "accept"
    assert is_stageable_concept("n") is True
    assert is_stageable_concept("slides.pdf") is False


def test_relation_policy_routes_inferred_and_related_to_to_candidate_store():
    from app.services.quality.policies import RelationQualityPolicy
    from app.services.quality.signals import build_quality_signals

    policy = RelationQualityPolicy()
    signals = build_quality_signals(
        target_type="relation",
        text="Residual Network prerequisite_of Augmenting Path",
        chunk_id="chunk-1",
        evidence_text="Residual Network is required before studying Augmenting Path algorithms.",
        source_name="Residual Network",
        target_name="Augmenting Path",
        support_count=2,
    )
    accepted = policy.decide(signals, relation_type="prerequisite_of", confidence=0.88, weight=0.8)
    assert accepted.action == "accept"

    related = policy.decide(signals, relation_type="related_to", confidence=0.91, weight=0.9)
    assert related.action == "candidate_only"
    assert "related_to_candidate_only" in related.reasons

    inferred = policy.decide(signals, relation_type="prerequisite_of", confidence=0.91, weight=0.9, inferred=True)
    assert inferred.action == "candidate_only"
    assert "inferred_candidate_only" in inferred.reasons


def test_domain_quality_profile_uses_representative_samples(sample_course, indexed_chunks):
    from app.services.quality.profiles import QUALITY_PROFILE_SCHEMA_VERSION, build_domain_quality_profile_payload

    _document, chunks = indexed_chunks
    payload = build_domain_quality_profile_payload(sample_course, chunks, sample_limit=8)

    assert payload["schema_version"] == QUALITY_PROFILE_SCHEMA_VERSION
    assert payload["sample_chunk_ids"] == [chunk.id for chunk in chunks]
    assert "relation_schema_hints" in payload
    assert payload["profile_hash"]


@pytest.mark.asyncio
async def test_quality_judge_uses_cache(monkeypatch):
    from app.services.quality import judge as judge_module
    from app.services.quality.judge import QualityJudge

    class FakeCache:
        def __init__(self) -> None:
            self.values = {}

        def get_quality_judgment(self, key):
            return self.values.get(key)

        def set_quality_judgment(self, key, result, ttl=86400):
            self.values[key] = result

    class FakeSettings:
        chat_model = "judge-model"

    class FakeProvider:
        settings = FakeSettings()
        calls = 0

        async def classify_json(self, _system_prompt, _user_prompt, fallback=None):
            self.calls += 1
            return {"action": "accept", "score": 0.9, "reasons": ["policy_passed"]}

    cache = FakeCache()
    provider = FakeProvider()
    monkeypatch.setattr(judge_module, "get_cache_manager", lambda: cache)

    judge = QualityJudge(provider=provider)
    candidate = {"name": "Residual Network", "signals": {"score": 0.9}}
    first = await judge.judge(course_id="course-1", profile={"version": "quality_profile_v1:test"}, target_type="concept", candidate=candidate)
    second = await judge.judge(course_id="course-1", profile={"version": "quality_profile_v1:test"}, target_type="concept", candidate=candidate)

    assert first["cached"] is False
    assert second["cached"] is True
    assert provider.calls == 1
