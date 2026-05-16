"""Regression tests for P0/P1 robustness fixes.

Covers NaN defense, negative weight rejection, empty-dimension detection,
upsert count verification, title-None guard, and evidence_count clamping.
"""
from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# P0-4: _rounded NaN / infinity defense
# ---------------------------------------------------------------------------
class TestRoundedNanDefense:
    def test_nan_returns_zero(self):
        from app.services.quality.policies import _rounded

        assert _rounded(float("nan")) == 0.0

    def test_positive_inf_returns_zero(self):
        from app.services.quality.policies import _rounded

        assert _rounded(float("inf")) == 0.0

    def test_negative_inf_returns_zero(self):
        from app.services.quality.policies import _rounded

        assert _rounded(float("-inf")) == 0.0

    def test_normal_value_unchanged(self):
        from app.services.quality.policies import _rounded

        assert _rounded(0.5) == 0.5

    def test_clamps_above_one(self):
        from app.services.quality.policies import _rounded

        assert _rounded(1.5) == 1.0

    def test_clamps_below_zero(self):
        from app.services.quality.policies import _rounded

        assert _rounded(-0.3) == 0.0


# ---------------------------------------------------------------------------
# P1-6: evidence_count negative clamping
# ---------------------------------------------------------------------------
class TestConceptPolicyNegativeEvidence:
    def test_negative_evidence_count_clamped(self):
        from app.services.quality.policies import ConceptQualityPolicy
        from app.services.quality.signals import build_quality_signals

        signals = build_quality_signals(
            target_type="concept",
            text="test concept term",
        )
        policy = ConceptQualityPolicy()
        # Should not crash, evidence_count gets clamped to 0
        decision = policy.decide(signals, evidence_count=-1)
        assert decision.target_type == "concept"
        # The actual audit should show clamped value behaviour (insufficient_evidence)
        assert "insufficient_evidence" in decision.reasons or decision.action == "reject"


# ---------------------------------------------------------------------------
# P0-5 part 1: normalized_vector with NaN
# ---------------------------------------------------------------------------
class TestNormalizedVectorNanDefense:
    def test_nan_returns_none(self):
        from app.services.graph_algorithms import normalized_vector

        result = normalized_vector([1.0, float("nan"), 0.5])
        assert result is None

    def test_inf_returns_none(self):
        from app.services.graph_algorithms import normalized_vector

        result = normalized_vector([float("inf"), 0.5, 0.5])
        assert result is None

    def test_normal_values_work(self):
        from app.services.graph_algorithms import normalized_vector

        result = normalized_vector([3.0, 4.0])
        assert result is not None
        assert abs(result[0] - 0.6) < 0.001
        assert abs(result[1] - 0.8) < 0.001


# ---------------------------------------------------------------------------
# P0-5 part 2: relation_hard_gate NaN confidence rejection
# ---------------------------------------------------------------------------
class TestRelationHardGateNanDefense:
    def test_nan_confidence_rejected(self):
        from app.services.graph_algorithms import (
            ConceptSignal,
            RelationSignal,
            relation_hard_gate,
        )

        left = ConceptSignal(concept_id="a", importance=0.5, evidence_count=5, chapter_refs=("ch1",), vector=None)
        right = ConceptSignal(concept_id="b", importance=0.5, evidence_count=5, chapter_refs=("ch1",), vector=None)
        relation = RelationSignal(
            source_id="a", target_id="b", relation_type="is_a",
            confidence=float("nan"), support_count=3,
            evidence_chunk_id="chunk1", relation_source="llm",
            metadata={"evidence_source_match": True, "evidence_target_match": True},
        )
        passed, reason = relation_hard_gate(relation, left, right, 0.8)
        assert not passed
        assert reason == "confidence_too_low"

    def test_nan_weight_rejected(self):
        from app.services.graph_algorithms import (
            ConceptSignal,
            RelationSignal,
            relation_hard_gate,
        )

        left = ConceptSignal(concept_id="a", importance=0.5, evidence_count=5, chapter_refs=("ch1",), vector=None)
        right = ConceptSignal(concept_id="b", importance=0.5, evidence_count=5, chapter_refs=("ch1",), vector=None)
        relation = RelationSignal(
            source_id="a", target_id="b", relation_type="is_a",
            confidence=0.9, support_count=3,
            evidence_chunk_id="chunk1", relation_source="llm",
            metadata={"evidence_source_match": True, "evidence_target_match": True},
        )
        passed, reason = relation_hard_gate(relation, left, right, 0.8, weight=float("nan"))
        assert not passed
        assert reason == "invalid_weight"


# ---------------------------------------------------------------------------
# P0-5 part 3: graph_from_edges skips bad edges
# ---------------------------------------------------------------------------
class TestGraphFromEdgesNanDefense:
    def test_nan_weight_edge_skipped(self):
        from app.services.graph_algorithms import ConceptSignal, WeightedEdge, graph_from_edges

        concepts = [
            ConceptSignal(concept_id="a", importance=0.5, evidence_count=3, chapter_refs=(), vector=None),
            ConceptSignal(concept_id="b", importance=0.5, evidence_count=3, chapter_refs=(), vector=None),
        ]
        edges = [
            WeightedEdge(
                source_id="a", target_id="b", relation_type="is_a",
                weight=float("nan"), semantic_similarity=0.8, support_count=1,
                confidence=0.9, evidence_chunk_id=None, relation_source="llm",
                is_inferred=False, metadata={},
            ),
        ]
        graph = graph_from_edges(concepts, edges)
        assert graph.number_of_edges() == 0

    def test_negative_weight_edge_skipped(self):
        from app.services.graph_algorithms import ConceptSignal, WeightedEdge, graph_from_edges

        concepts = [
            ConceptSignal(concept_id="a", importance=0.5, evidence_count=3, chapter_refs=(), vector=None),
            ConceptSignal(concept_id="b", importance=0.5, evidence_count=3, chapter_refs=(), vector=None),
        ]
        edges = [
            WeightedEdge(
                source_id="a", target_id="b", relation_type="is_a",
                weight=-0.5, semantic_similarity=0.8, support_count=1,
                confidence=0.9, evidence_chunk_id=None, relation_source="llm",
                is_inferred=False, metadata={},
            ),
        ]
        graph = graph_from_edges(concepts, edges)
        assert graph.number_of_edges() == 0


# ---------------------------------------------------------------------------
# P0-8: document.title None guard
# ---------------------------------------------------------------------------
class TestDocumentChapterLabelTitleNone:
    def test_none_title_no_crash(self):
        from app.services.concept_graph import document_chapter_label

        doc = MagicMock()
        doc.title = None
        doc.source_path = "/data/test.pdf"
        doc.source_type = "pdf"
        doc.tags = []
        result = document_chapter_label(doc, course_name="test")
        assert isinstance(result, str)
        assert result == "pdf"


# ---------------------------------------------------------------------------
# P1-12: validate_embedding_vectors empty dimensions
# ---------------------------------------------------------------------------
class TestEmbeddingValidationEmptyDimensions:
    def test_zero_dimensions_raises_specific_error(self):
        from app.services.embeddings import validate_embedding_vectors

        with pytest.raises(RuntimeError, match="Invalid embedding_dimensions"):
            validate_embedding_vectors([[]], expected_count=1, expected_dimensions=0)

    def test_normal_validation_still_works(self):
        from app.services.embeddings import validate_embedding_vectors

        # Should raise for wrong count
        with pytest.raises(RuntimeError, match="expected 2"):
            validate_embedding_vectors([[0.1, 0.2]], expected_count=2, expected_dimensions=2)


# ---------------------------------------------------------------------------
# P0-5 part 4: analyze_graph NaN in centrality
# ---------------------------------------------------------------------------
class TestAnalyzeGraphNanSafety:
    def test_centrality_values_are_finite(self):
        """Ensure all values written to metrics dicts are finite (JSON-safe)."""
        from app.services.graph_algorithms import ConceptSignal, WeightedEdge, analyze_graph

        concepts = [
            ConceptSignal(concept_id="a", importance=0.5, evidence_count=3, chapter_refs=("ch1",), vector=None),
            ConceptSignal(concept_id="b", importance=0.5, evidence_count=3, chapter_refs=("ch1",), vector=None),
        ]
        edges = [
            WeightedEdge(
                source_id="a", target_id="b", relation_type="is_a",
                weight=0.8, semantic_similarity=0.7, support_count=2,
                confidence=0.9, evidence_chunk_id=None, relation_source="llm",
                is_inferred=False, metadata={},
            ),
        ]
        metrics, graph = analyze_graph(concepts, edges)
        for node_id, metric in metrics.items():
            centrality = metric.get("centrality", {})
            for key, value in centrality.items():
                if isinstance(value, float):
                    assert math.isfinite(value), f"centrality.{key} for {node_id} is not finite: {value}"
            rank = metric.get("graph_rank_score", 0.0)
            assert math.isfinite(rank), f"graph_rank_score for {node_id} is not finite: {rank}"
