from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
import re
from typing import Any

from app.services.quality.signals import QualitySignals


@dataclass(frozen=True)
class QualityDecision:
    target_type: str
    action: str
    score: float
    reasons: list[str] = field(default_factory=list)
    audit: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


def _rounded(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return round(max(0.0, min(1.0, value)), 4)


def _chunk_route(action: str, *, retain: bool = True) -> dict[str, bool]:
    if not retain or action == "discard":
        return {
            "embed": False,
            "retrieval": False,
            "graph_extraction": False,
            "summary": False,
            "evidence_only": False,
        }
    if action == "graph_candidate":
        return {"embed": True, "retrieval": True, "graph_extraction": True, "summary": True, "evidence_only": False}
    if action == "retrieval_candidate":
        return {"embed": True, "retrieval": True, "graph_extraction": False, "summary": True, "evidence_only": False}
    if action == "summary_only":
        return {"embed": True, "retrieval": False, "graph_extraction": False, "summary": True, "evidence_only": True}
    if action == "evidence_only":
        return {"embed": True, "retrieval": True, "graph_extraction": False, "summary": False, "evidence_only": True}
    return {"embed": True, "retrieval": False, "graph_extraction": False, "summary": False, "evidence_only": False}


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", text or ""))


def _has_math_letter_or_symbol(text: str) -> bool:
    return bool(re.search(r"[\u0370-\u03ff\u2200-\u22ff+\-/*^=<>]", text or ""))


def _is_recall_safe_short_concept(text: str, *, evidence_count: int, specificity_score: float | None) -> bool:
    compact = (text or "").replace(" ", "")
    if not compact:
        return False
    if _has_cjk(compact):
        return len(compact) >= 1 and evidence_count >= 1
    if _has_math_letter_or_symbol(compact):
        return len(compact) >= 2 and (evidence_count >= 1 or (specificity_score or 0.0) >= 0.45)
    return False


def _is_structural_course_label(text: str) -> bool:
    normalized = (text or "").strip().lower()
    if normalized in {"homework", "assignment", "solution", "solutions", "exercise", "exercises", "slides", "notes", "worksheet"}:
        return True
    return bool(
        re.fullmatch(
            r"(?:week|lecture|chapter|section|slide|page|lab)\s+\d+[a-z]?(?:\s+(?:solutions?|answers?|questions?|notes?|slides?|review|summary|worksheet))?",
            normalized,
        )
    )


class ChunkQualityPolicy:
    def decide(self, signals: QualitySignals, *, section_name: str | None = None, section_title: str | None = None) -> QualityDecision:
        text = signals.text_quality
        semantic = signals.semantic_density
        structural = signals.structural_role
        reasons: list[str] = []

        if text.normalized_length == 0:
            reasons.append("empty_chunk")
        elif text.normalized_length < 40:
            reasons.append("too_short_for_chunk")
        if text.mojibake_ratio > 0.08:
            reasons.append("severe_mojibake_noise")
        elif text.mojibake_ratio > 0.01:
            reasons.append("mojibake_noise")
        if text.control_char_count >= max(8, int(max(text.length, 1) * 0.05)):
            reasons.append("control_char_noise")
        if text.repeated_line_ratio >= 0.92 and text.normalized_length >= 120:
            reasons.append("repeated_extraction_noise")
        if text.toc_like:
            reasons.append("toc_layout_noise")
        if "output" in structural.roles:
            reasons.append("notebook_output")

        score = (
            0.30 * min(1.0, text.normalized_length / 600)
            + 0.25 * semantic.term_density
            + 0.20 * semantic.unique_token_ratio
            + 0.15 * semantic.definition_score
            + 0.05 * float(semantic.has_formula)
            + 0.05 * float(semantic.has_table)
        )
        score -= 0.35 * float(text.toc_like)
        score -= 0.40 * min(1.0, text.mojibake_ratio * 20)

        hard_discard_reasons = {"empty_chunk", "severe_mojibake_noise", "control_char_noise", "repeated_extraction_noise"}
        retain = not bool(set(reasons).intersection(hard_discard_reasons))
        retention_reason = "retained_for_downstream_routing" if retain else "mechanical_noise"

        if not retain:
            action = "discard"
        elif text.toc_like or "structural_label" in structural.roles:
            action = "summary_only"
        elif "output" in structural.roles:
            action = "evidence_only"
        elif text.normalized_length < 40 and not (semantic.has_formula or semantic.has_table or semantic.definition_score):
            action = "evidence_only"
        elif "code" in structural.roles and not _is_kept_code_section(section_name, section_title):
            action = "embed_only"
            reasons.append("code_without_domain_context")
        elif semantic.definition_score or semantic.entity_density >= 0.08 or semantic.term_density >= 0.20:
            action = "graph_candidate"
        elif semantic.has_formula or semantic.has_table:
            action = "retrieval_candidate"
        else:
            action = "retrieval_candidate" if score >= 0.25 else "embed_only"

        return QualityDecision(
            target_type="chunk",
            action=action,
            score=_rounded(score),
            reasons=reasons or ["policy_passed"],
            audit={
                "signals": signals.model_dump(),
                "section_name": section_name,
                "section_title": section_title,
                "retention_decision": {"retain": retain, "reason": retention_reason},
                "route_eligibility": _chunk_route(action, retain=retain),
            },
        )


class ConceptQualityPolicy:
    def decide(self, signals: QualitySignals, *, evidence_count: int = 0, existing: bool = False, specificity_score: float | None = None) -> QualityDecision:
        evidence_count = max(0, evidence_count)
        text = signals.text_quality
        structural = signals.structural_role
        semantic = signals.semantic_density
        domain = signals.domain_specificity
        reasons: list[str] = []

        if not signals.normalized_text:
            reasons.append("empty")
        recall_safe_short = _is_recall_safe_short_concept(
            signals.normalized_text,
            evidence_count=evidence_count,
            specificity_score=specificity_score,
        )
        if text.normalized_length < 3 and not recall_safe_short:
            reasons.append("too_short")
        if text.mojibake_ratio > 0.04:
            reasons.append("mojibake_noise")
        if structural.path_or_filename:
            reasons.append("path_or_filename")
        if ("structural_label" in structural.roles or _is_structural_course_label(signals.normalized_text)) and semantic.has_formula is False:
            reasons.append("structural_container")
        if domain.genericity_score >= 1.0 and evidence_count <= 2:
            reasons.append("generic_low_specificity")
        if evidence_count < 2 and not existing:
            reasons.append("insufficient_evidence")

        score = specificity_score if specificity_score is not None else domain.specificity_score
        score = max(score, 0.35 * semantic.definition_score + 0.25 * semantic.term_density + 0.20 * semantic.entity_density)
        score -= 0.35 * structural.structural_score
        score -= 0.25 * domain.genericity_score
        accepted = not reasons and (existing or evidence_count >= 2) and score >= 0.45
        if existing and reasons == ["insufficient_evidence"]:
            accepted = True
            reasons = ["existing_concept"]

        return QualityDecision(
            target_type="concept",
            action="accept" if accepted else "reject",
            score=_rounded(score),
            reasons=reasons or ["policy_passed"],
            audit={"signals": signals.model_dump(), "evidence_count": evidence_count, "existing": existing},
        )


class RelationQualityPolicy:
    def decide(
        self,
        signals: QualitySignals,
        *,
        relation_type: str,
        confidence: float,
        weight: float | None = None,
        inferred: bool = False,
        min_confidence: float = 0.72,
    ) -> QualityDecision:
        evidence = signals.evidence_grounding
        reasons: list[str] = []
        if inferred:
            reasons.append("inferred_candidate_only")
        if relation_type == "related_to":
            reasons.append("related_to_candidate_only")
        if confidence < min_confidence:
            reasons.append("confidence_too_low")
        if not evidence.has_chunk and not evidence.has_text_span:
            reasons.append("missing_evidence_span")
        if relation_type != "related_to" and not (evidence.source_match and evidence.target_match):
            reasons.append("evidence_endpoint_mismatch")
        if weight is not None and weight < 0.62:
            reasons.append("weight_below_threshold")

        score = 0.40 * confidence + 0.25 * float(evidence.source_match) + 0.25 * float(evidence.target_match)
        score += 0.10 * min(1.0, evidence.support_count / 3)
        if weight is not None:
            score = max(score, weight)

        accepted = not reasons
        action = "accept" if accepted else "candidate_only"
        if relation_type == "related_to" or inferred:
            action = "candidate_only"
        return QualityDecision(
            target_type="relation",
            action=action,
            score=_rounded(score),
            reasons=reasons or ["policy_passed"],
            audit={
                "signals": signals.model_dump(),
                "relation_type": relation_type,
                "confidence": confidence,
                "min_confidence": min_confidence,
                "weight": weight,
                "inferred": inferred,
            },
        )


def _is_kept_code_section(section_name: str | None, section_title: str | None) -> bool:
    text = f"{section_name or ''} {section_title or ''}".lower()
    return any(marker in text for marker in ("centrality", "community", "random network", "configuration model", "algorithm", "graph"))
