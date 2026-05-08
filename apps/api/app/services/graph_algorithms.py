from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import networkx as nx
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.embeddings import ChatProvider
from app.services.vector_store import VectorStore, cosine_similarity


MIN_GRAPH_CONCEPTS = 160
MAX_GRAPH_CONCEPTS = 360
SEMANTIC_EDGE_THRESHOLD = 0.62
DIJKSTRA_SEMANTIC_THRESHOLD = 0.78
ALLOWED_COMPLETION_RELATIONS = {
    "defines",
    "relates_to",
    "prerequisite_of",
    "example_of",
    "solves",
    "compares",
    "extends",
    "mentions",
}


@dataclass(frozen=True)
class ConceptSignal:
    concept_id: str
    importance: float
    evidence_count: int
    chapter_refs: tuple[str, ...]
    vector: tuple[float, ...] | None


@dataclass(frozen=True)
class RelationSignal:
    source_id: str
    target_id: str
    relation_type: str
    confidence: float
    support_count: int = 1
    evidence_chunk_id: str | None = None
    relation_source: str = "llm"
    is_inferred: bool = False
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class WeightedEdge:
    source_id: str
    target_id: str
    relation_type: str
    weight: float
    semantic_similarity: float
    support_count: int
    confidence: float
    evidence_chunk_id: str | None
    relation_source: str
    is_inferred: bool
    metadata: dict[str, Any]


def clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def dynamic_k(evidence_count: int) -> int:
    return int(clamp(4 + math.floor(math.log2(1 + max(evidence_count, 0))), 4, 12))


def dynamic_r(chapter_ref_count: int) -> int:
    return int(clamp(2 + math.floor(math.log2(1 + max(chapter_ref_count, 0))), 2, 8))


def edge_key(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))


def normalized_vector(values: list[float]) -> tuple[float, ...] | None:
    magnitude = math.sqrt(sum(float(value) * float(value) for value in values))
    if magnitude <= 1e-12:
        return None
    return tuple(float(value) / magnitude for value in values)


def average_vectors(vectors: list[list[float]]) -> tuple[float, ...] | None:
    if not vectors:
        return None
    width = len(vectors[0])
    usable = [vector for vector in vectors if len(vector) == width]
    if not usable:
        return None
    averaged = [sum(vector[index] for vector in usable) / len(usable) for index in range(width)]
    return normalized_vector(averaged)


def safe_float(value: Any, default: float = 0.5) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def exception_summary(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return f"{type(exc).__name__}: {exc!r}"


def similarity(left: tuple[float, ...] | None, right: tuple[float, ...] | None) -> float:
    if left is None or right is None:
        return 0.0
    return float(cosine_similarity(list(left), list(right)))


def structure_score(left: ConceptSignal, right: ConceptSignal) -> float:
    left_refs = set(left.chapter_refs)
    right_refs = set(right.chapter_refs)
    if left_refs and right_refs and left_refs.intersection(right_refs):
        return 1.0
    if left_refs or right_refs:
        return 0.35
    return 0.0


def evidence_score(left: ConceptSignal, right: ConceptSignal) -> float:
    return clamp(math.log1p(left.evidence_count + right.evidence_count) / math.log1p(10), 0.0, 1.0)


def weighted_score(left: ConceptSignal, right: ConceptSignal, llm_confidence: float, semantic: float) -> float:
    raw = (
        0.45 * clamp(llm_confidence, 0.0, 1.0)
        + 0.30 * clamp((semantic + 1.0) / 2.0, 0.0, 1.0)
        + 0.15 * evidence_score(left, right)
        + 0.10 * structure_score(left, right)
    )
    return clamp(raw, 0.0, 1.0)


def build_sparse_edges(concepts: list[ConceptSignal], relations: list[RelationSignal]) -> list[WeightedEdge]:
    concept_index = {concept.concept_id: concept for concept in concepts}
    llm_by_pair: dict[tuple[str, str], RelationSignal] = {}
    for relation in relations:
        if relation.source_id not in concept_index or relation.target_id not in concept_index or relation.source_id == relation.target_id:
            continue
        key = edge_key(relation.source_id, relation.target_id)
        current = llm_by_pair.get(key)
        if current is None or relation.confidence > current.confidence:
            llm_by_pair[key] = relation

    similarities: dict[tuple[str, str], float] = {}
    outgoing: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for index, left in enumerate(concepts):
        for right in concepts[index + 1 :]:
            semantic = similarity(left.vector, right.vector)
            similarities[edge_key(left.concept_id, right.concept_id)] = semantic
            outgoing[left.concept_id].append((right.concept_id, semantic))
            outgoing[right.concept_id].append((left.concept_id, semantic))

    selected_out: dict[str, set[str]] = defaultdict(set)
    inbound_candidates: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for concept in concepts:
        ranked = sorted(outgoing[concept.concept_id], key=lambda item: item[1], reverse=True)
        for target_id, semantic in ranked[: dynamic_k(concept.evidence_count)]:
            if semantic < SEMANTIC_EDGE_THRESHOLD:
                continue
            selected_out[concept.concept_id].add(target_id)
            inbound_candidates[target_id].append((concept.concept_id, semantic))

    accepted_in: dict[str, set[str]] = defaultdict(set)
    for concept in concepts:
        ranked_in = sorted(inbound_candidates[concept.concept_id], key=lambda item: item[1], reverse=True)
        for source_id, _semantic in ranked_in[: dynamic_r(len(concept.chapter_refs))]:
            accepted_in[concept.concept_id].add(source_id)

    edge_pairs: set[tuple[str, str]] = set(llm_by_pair)
    for source_id, targets in selected_out.items():
        for target_id in targets:
            mutual = source_id in selected_out.get(target_id, set())
            reciprocal_accept = source_id in accepted_in.get(target_id, set()) or target_id in accepted_in.get(source_id, set())
            if mutual or reciprocal_accept:
                edge_pairs.add(edge_key(source_id, target_id))

    weighted_edges: list[WeightedEdge] = []
    for pair in sorted(edge_pairs):
        source_id, target_id = pair
        left = concept_index[source_id]
        right = concept_index[target_id]
        llm_relation = llm_by_pair.get(pair)
        semantic = similarities.get(pair, 0.0)
        confidence = llm_relation.confidence if llm_relation else 0.0
        weight = weighted_score(left, right, confidence, semantic)
        if not llm_relation and weight < 0.42:
            continue
        weighted_edges.append(
            WeightedEdge(
                source_id=llm_relation.source_id if llm_relation else source_id,
                target_id=llm_relation.target_id if llm_relation else target_id,
                relation_type=llm_relation.relation_type if llm_relation else "relates_to",
                weight=weight,
                semantic_similarity=semantic,
                support_count=llm_relation.support_count if llm_relation else min(left.evidence_count, right.evidence_count),
                confidence=confidence if llm_relation else round(weight, 4),
                evidence_chunk_id=llm_relation.evidence_chunk_id if llm_relation else None,
                relation_source=llm_relation.relation_source if llm_relation else "semantic_sparse",
                is_inferred=llm_relation.is_inferred if llm_relation else True,
                metadata=llm_relation.metadata or {} if llm_relation else {"semantic_sparse": True},
            )
        )
    return weighted_edges


def graph_from_edges(concepts: list[ConceptSignal], edges: list[WeightedEdge]) -> nx.Graph:
    graph = nx.Graph()
    for concept in concepts:
        graph.add_node(concept.concept_id, importance=concept.importance, evidence_count=concept.evidence_count)
    for edge in edges:
        graph.add_edge(edge.source_id, edge.target_id, weight=edge.weight, semantic_similarity=edge.semantic_similarity)
    return graph


def _deterministic_kmeans(matrix: np.ndarray, k: int, iterations: int = 16) -> list[int]:
    if len(matrix) == 0:
        return []
    if k <= 1:
        return [0 for _ in range(len(matrix))]
    centers = matrix[np.linspace(0, len(matrix) - 1, k, dtype=int)].copy()
    labels = np.zeros(len(matrix), dtype=int)
    for _ in range(iterations):
        distances = np.linalg.norm(matrix[:, None, :] - centers[None, :, :], axis=2)
        labels = distances.argmin(axis=1)
        for center_index in range(k):
            members = matrix[labels == center_index]
            if len(members):
                centers[center_index] = members.mean(axis=0)
    return [int(label) for label in labels]


def spectral_labels_for_graph(graph: nx.Graph) -> dict[str, int]:
    labels: dict[str, int] = {node_id: 0 for node_id in graph.nodes}
    large_components = [component for component in nx.connected_components(graph) if len(component) >= 8]
    next_label = 0
    for component in sorted(large_components, key=len, reverse=True):
        subgraph = graph.subgraph(component)
        nodes = list(subgraph.nodes)
        k = int(clamp(round(math.sqrt(len(nodes)) / 2), 2, min(8, len(nodes) - 1)))
        try:
            laplacian = nx.normalized_laplacian_matrix(subgraph, weight="weight")
            from scipy.sparse.linalg import eigsh

            _values, vectors = eigsh(laplacian, k=k, which="SM")
            component_labels = _deterministic_kmeans(np.asarray(vectors), k)
        except Exception:
            component_labels = [0 if index < len(nodes) / 2 else 1 for index in range(len(nodes))]
        label_offset = next_label
        for node_id, label in zip(nodes, component_labels):
            labels[node_id] = label_offset + label
        next_label += max(component_labels, default=0) + 1
    return labels


def analyze_graph(concepts: list[ConceptSignal], edges: list[WeightedEdge]) -> tuple[dict[str, dict[str, Any]], nx.Graph]:
    graph = graph_from_edges(concepts, edges)
    if graph.number_of_nodes() == 0:
        return {}, graph

    components = sorted(nx.connected_components(graph), key=len, reverse=True)
    component_by_node = {node_id: index for index, component in enumerate(components) for node_id in component}
    try:
        communities = nx.algorithms.community.louvain_communities(graph, weight="weight", seed=42)
    except Exception:
        communities = components
    louvain_by_node = {node_id: index for index, community in enumerate(communities) for node_id in community}
    spectral_by_node = spectral_labels_for_graph(graph)
    degree = nx.degree_centrality(graph)
    weighted_degree = {
        node_id: sum(float(data.get("weight", 0.0)) for _source, _target, data in graph.edges(node_id, data=True))
        for node_id in graph.nodes
    }
    if graph.number_of_edges():
        pagerank = nx.pagerank(graph, weight="weight")
        betweenness = nx.betweenness_centrality(graph, weight="weight", normalized=True)
        closeness = nx.closeness_centrality(graph, distance=lambda _u, _v, data: 1 / (0.05 + float(data.get("weight", 0.0))))
    else:
        pagerank = {node_id: 0.0 for node_id in graph.nodes}
        betweenness = {node_id: 0.0 for node_id in graph.nodes}
        closeness = {node_id: 0.0 for node_id in graph.nodes}
    max_weighted_degree = max(weighted_degree.values(), default=1.0) or 1.0

    metrics: dict[str, dict[str, Any]] = {}
    concept_index = {concept.concept_id: concept for concept in concepts}
    for node_id in graph.nodes:
        signal = concept_index[node_id]
        weighted_degree_norm = weighted_degree[node_id] / max_weighted_degree
        centrality_score = clamp(
            0.25 * degree.get(node_id, 0.0)
            + 0.25 * weighted_degree_norm
            + 0.20 * pagerank.get(node_id, 0.0)
            + 0.20 * betweenness.get(node_id, 0.0)
            + 0.10 * closeness.get(node_id, 0.0),
            0.0,
            1.0,
        )
        graph_rank_score = clamp(
            0.50 * centrality_score + 0.25 * signal.importance + 0.25 * clamp(math.log1p(signal.evidence_count) / math.log1p(10), 0.0, 1.0),
            0.0,
            1.0,
        )
        metrics[node_id] = {
            "component_id": component_by_node.get(node_id, 0),
            "community_louvain": louvain_by_node.get(node_id, 0),
            "community_spectral": spectral_by_node.get(node_id, 0),
            "centrality": {
                "degree": degree.get(node_id, 0.0),
                "weighted_degree": weighted_degree.get(node_id, 0.0),
                "pagerank": pagerank.get(node_id, 0.0),
                "betweenness": betweenness.get(node_id, 0.0),
                "closeness": closeness.get(node_id, 0.0),
                "centrality_score": centrality_score,
            },
            "graph_rank_score": graph_rank_score,
        }
    return metrics, graph


def infer_dijkstra_edges(graph: nx.Graph, concepts: list[ConceptSignal], existing_edges: list[WeightedEdge]) -> list[WeightedEdge]:
    concept_index = {concept.concept_id: concept for concept in concepts}
    existing_pairs = {edge_key(edge.source_id, edge.target_id) for edge in existing_edges}
    inferred: list[WeightedEdge] = []
    if graph.number_of_nodes() < 3 or graph.number_of_edges() == 0:
        return inferred

    def cost(_source: str, _target: str, data: dict[str, Any]) -> float:
        return 1 / (0.05 + float(data.get("weight", 0.0)))

    nodes = list(graph.nodes)
    for source_id in nodes:
        lengths, paths = nx.single_source_dijkstra(graph, source_id, cutoff=7.5, weight=cost)
        for target_id, path in paths.items():
            if source_id >= target_id or len(path) not in {3, 4}:
                continue
            pair = edge_key(source_id, target_id)
            if pair in existing_pairs:
                continue
            left = concept_index[source_id]
            right = concept_index[target_id]
            semantic = similarity(left.vector, right.vector)
            if semantic < DIJKSTRA_SEMANTIC_THRESHOLD:
                continue
            path_weight = 1 / (1 + float(lengths[target_id]))
            edge_weight = weighted_score(left, right, path_weight, semantic)
            if edge_weight < 0.48:
                continue
            inferred.append(
                WeightedEdge(
                    source_id=source_id,
                    target_id=target_id,
                    relation_type="relates_to",
                    weight=edge_weight,
                    semantic_similarity=semantic,
                    support_count=min(left.evidence_count, right.evidence_count),
                    confidence=round(edge_weight, 4),
                    evidence_chunk_id=None,
                    relation_source="dijkstra_inferred",
                    is_inferred=True,
                    metadata={"path": path, "path_cost": float(lengths[target_id])},
                )
            )
    return sorted(inferred, key=lambda edge: edge.weight, reverse=True)[: max(20, len(concepts) // 5)]


def select_concepts_to_keep(concepts: list[ConceptSignal], metrics: dict[str, dict[str, Any]], graph: nx.Graph) -> set[str]:
    available = len(concepts)
    if available <= MAX_GRAPH_CONCEPTS:
        floor = min(MIN_GRAPH_CONCEPTS, available)
    else:
        floor = MIN_GRAPH_CONCEPTS
    keep_limit = min(MAX_GRAPH_CONCEPTS, available)
    noisy = {
        concept.concept_id
        for concept in concepts
        if graph.degree(concept.concept_id) == 0 and concept.evidence_count <= 1 and concept.importance < 0.55
    }
    ranked = sorted(
        concepts,
        key=lambda concept: (
            metrics.get(concept.concept_id, {}).get("graph_rank_score", 0.0),
            concept.importance,
            concept.evidence_count,
        ),
        reverse=True,
    )
    keep: list[str] = [concept.concept_id for concept in ranked if concept.concept_id not in noisy][:keep_limit]
    if len(keep) < floor:
        for concept in ranked:
            if concept.concept_id not in keep:
                keep.append(concept.concept_id)
            if len(keep) >= floor:
                break
    return set(keep)


def relation_signals_from_db(relations: list[ConceptRelation]) -> list[RelationSignal]:
    from app.models import ConceptRelation

    by_key: dict[tuple[str, str, str], RelationSignal] = {}
    for relation in relations:
        if not relation.target_concept_id:
            continue
        key = (relation.source_concept_id, relation.target_concept_id, relation.relation_type)
        current = by_key.get(key)
        support = int(getattr(relation, "support_count", 1) or 1)
        signal = RelationSignal(
            source_id=relation.source_concept_id,
            target_id=relation.target_concept_id,
            relation_type=relation.relation_type,
            confidence=float(relation.confidence or 0.0),
            support_count=support,
            evidence_chunk_id=relation.evidence_chunk_id,
            relation_source=getattr(relation, "relation_source", None) or relation.extraction_method or "llm",
            is_inferred=bool(getattr(relation, "is_inferred", False)),
            metadata=getattr(relation, "metadata_json", None) or {},
        )
        if current is None or signal.confidence > current.confidence:
            by_key[key] = signal
    return list(by_key.values())


def completion_relation_signals(payload: Any, by_name: dict[str, Any]) -> list[RelationSignal]:
    if not isinstance(payload, dict):
        return []
    raw_relations = payload.get("relations", [])
    if not isinstance(raw_relations, list):
        return []

    signals: list[RelationSignal] = []
    seen: set[tuple[str, str, str]] = set()
    for relation in raw_relations:
        if not isinstance(relation, dict):
            continue
        source_name = relation.get("source")
        target_name = relation.get("target")
        relation_type = relation.get("relation_type", "relates_to")
        if not isinstance(source_name, str) or not isinstance(target_name, str) or relation_type not in ALLOWED_COMPLETION_RELATIONS:
            continue
        source = by_name.get(source_name.strip())
        target = by_name.get(target_name.strip())
        if not source or not target or source.id == target.id:
            continue
        key = (source.id, target.id, relation_type)
        if key in seen:
            continue
        seen.add(key)
        signals.append(
            RelationSignal(
                source_id=source.id,
                target_id=target.id,
                relation_type=relation_type,
                confidence=clamp(safe_float(relation.get("confidence"), 0.5), 0.0, 1.0),
                relation_source="llm_traversal_completion",
                metadata={"completion": True},
            )
        )
    return signals


def child_chunk_filter(chunk: Chunk) -> bool:
    return bool(chunk.is_active) and not bool((chunk.metadata_json or {}).get("is_parent"))


def collect_concept_signals(db: Session, course: Course, concepts: list[Concept], relations: list[ConceptRelation]) -> list[ConceptSignal]:
    from app.models import Chunk

    active_chunks = db.scalars(select(Chunk).where(Chunk.course_id == course.id, Chunk.is_active.is_(True))).all()
    child_chunks = [chunk for chunk in active_chunks if child_chunk_filter(chunk)]
    evidence_by_concept: dict[str, set[str]] = defaultdict(set)
    for relation in relations:
        if relation.evidence_chunk_id:
            evidence_by_concept[relation.source_concept_id].add(relation.evidence_chunk_id)
            if relation.target_concept_id:
                evidence_by_concept[relation.target_concept_id].add(relation.evidence_chunk_id)

    searchable_chunks = child_chunks[:]
    for concept in concepts:
        if evidence_by_concept.get(concept.id):
            continue
        normalized = (concept.normalized_name or "").lower()
        if not normalized:
            continue
        for chunk in searchable_chunks:
            haystack = f"{chunk.section or ''}\n{chunk.snippet or ''}\n{chunk.content[:1200]}".lower()
            if normalized in haystack:
                evidence_by_concept[concept.id].add(chunk.id)
                if len(evidence_by_concept[concept.id]) >= 4:
                    break

    vector_ids = sorted({chunk_id for ids in evidence_by_concept.values() for chunk_id in ids})
    point_vectors: dict[str, list[float]] = {}
    if vector_ids:
        vector_store = VectorStore(course_name=course.name)
        for index in range(0, len(vector_ids), 128):
            for point in vector_store.get_points(vector_ids[index : index + 128]):
                vector = point.get("vector")
                if isinstance(vector, list):
                    point_vectors[str(point["id"])] = vector

    signals: list[ConceptSignal] = []
    for concept in concepts:
        ids = evidence_by_concept.get(concept.id, set())
        vectors = [point_vectors[chunk_id] for chunk_id in ids if chunk_id in point_vectors]
        signals.append(
            ConceptSignal(
                concept_id=concept.id,
                importance=float(concept.importance_score or 0.0),
                evidence_count=len(ids),
                chapter_refs=tuple(concept.chapter_refs or []),
                vector=average_vectors(vectors),
            )
        )
    return signals


async def complete_relations_with_llm(
    db: Session,
    course_id: str,
    concepts_by_id: dict[str, Concept],
    graph: nx.Graph,
    metrics: dict[str, dict[str, Any]],
    limit: int = 8,
) -> list[RelationSignal]:
    from app.models import Chunk, ConceptRelation

    ranked_ids = sorted(metrics, key=lambda node_id: metrics[node_id]["graph_rank_score"], reverse=True)[:limit]
    if not ranked_ids:
        return []
    evidence_chunk_ids = {
        relation.evidence_chunk_id
        for relation in db.scalars(select(ConceptRelation).where(ConceptRelation.course_id == course_id)).all()
        if relation.evidence_chunk_id
    }
    chunks = {
        chunk.id: chunk
        for chunk in db.scalars(select(Chunk).where(Chunk.id.in_(evidence_chunk_ids))).all()
    } if evidence_chunk_ids else {}
    candidates: list[dict[str, Any]] = []
    for node_id in ranked_ids:
        neighbors = set(nx.single_source_shortest_path_length(graph, node_id, cutoff=2).keys())
        names = [concepts_by_id[item].canonical_name for item in neighbors if item in concepts_by_id]
        snippets = []
        for relation in db.scalars(
            select(ConceptRelation).where(
                ConceptRelation.course_id == course_id,
                ConceptRelation.source_concept_id.in_(neighbors),
            )
        ).all():
            chunk = chunks.get(relation.evidence_chunk_id or "")
            if chunk is not None:
                snippets.append(chunk.snippet[:300])
            if len(snippets) >= 5:
                break
        candidates.append({"center": concepts_by_id[node_id].canonical_name, "concepts": names[:18], "evidence": snippets})

    system_prompt = "You complete course graph relations from supplied evidence only. Return strict JSON."
    user_prompt = (
        "Return JSON with keys concepts and relations. Reuse only concept names shown below. "
        "Each relation must be directly supported by the evidence snippets; do not infer unsupported facts. "
        f"Allowed relation_type values: {sorted(ALLOWED_COMPLETION_RELATIONS)}.\n\n"
        f"{candidates}"
    )
    payload = await ChatProvider().classify_json(system_prompt, user_prompt, fallback={})
    by_name = {concept.canonical_name: concept for concept in concepts_by_id.values()}
    return completion_relation_signals(payload, by_name)


def upsert_weighted_edges(db: Session, course_id: str, edges: list[WeightedEdge]) -> int:
    from app.models import Concept, ConceptRelation

    existing = db.scalars(select(ConceptRelation).where(ConceptRelation.course_id == course_id)).all()
    by_exact = {
        (relation.source_concept_id, relation.target_concept_id, relation.relation_type): relation
        for relation in existing
        if relation.target_concept_id
    }
    changed = 0
    for edge in edges:
        key = (edge.source_id, edge.target_id, edge.relation_type)
        relation = by_exact.get(key)
        if relation is None:
            target = db.get(Concept, edge.target_id)
            if target is None:
                continue
            relation = ConceptRelation(
                course_id=course_id,
                source_concept_id=edge.source_id,
                target_concept_id=edge.target_id,
                target_name=target.canonical_name,
                relation_type=edge.relation_type,
                evidence_chunk_id=edge.evidence_chunk_id,
                confidence=edge.confidence,
                extraction_method=edge.relation_source,
                is_validated=edge.confidence >= 0.82 and not edge.is_inferred,
            )
            db.add(relation)
            by_exact[key] = relation
        relation.weight = max(float(getattr(relation, "weight", 0.0) or 0.0), edge.weight)
        relation.semantic_similarity = max(float(getattr(relation, "semantic_similarity", 0.0) or 0.0), edge.semantic_similarity)
        relation.support_count = max(int(getattr(relation, "support_count", 1) or 1), int(edge.support_count or 1))
        relation.relation_source = edge.relation_source
        relation.is_inferred = bool(edge.is_inferred)
        relation.metadata_json = {**(getattr(relation, "metadata_json", None) or {}), **edge.metadata}
        if not relation.evidence_chunk_id and edge.evidence_chunk_id:
            relation.evidence_chunk_id = edge.evidence_chunk_id
        changed += 1
    db.flush()
    return changed


def write_concept_metrics(
    db: Session,
    concepts: list[Concept],
    signals: list[ConceptSignal],
    metrics: dict[str, dict[str, Any]],
    keep_ids: set[str],
) -> int:
    from app.models import Concept, ConceptAlias, ConceptRelation

    signals_by_id = {signal.concept_id: signal for signal in signals}
    updated = 0
    for concept in concepts:
        if concept.id not in keep_ids:
            continue
        signal = signals_by_id.get(concept.id)
        metric = metrics.get(concept.id, {})
        concept.evidence_count = signal.evidence_count if signal else 0
        concept.community_louvain = metric.get("community_louvain")
        concept.community_spectral = metric.get("community_spectral")
        concept.component_id = metric.get("component_id")
        concept.centrality_json = metric.get("centrality", {})
        concept.graph_rank_score = float(metric.get("graph_rank_score", 0.0))
        updated += 1
    pruned_ids = {concept.id for concept in concepts} - keep_ids
    if pruned_ids:
        db.query(ConceptRelation).filter(
            ConceptRelation.course_id == concepts[0].course_id,
            (ConceptRelation.source_concept_id.in_(pruned_ids) | ConceptRelation.target_concept_id.in_(pruned_ids)),
        ).delete(synchronize_session=False)
        db.query(ConceptAlias).filter(ConceptAlias.concept_id.in_(pruned_ids)).delete(synchronize_session=False)
        db.query(Concept).filter(Concept.id.in_(pruned_ids)).delete(synchronize_session=False)
    db.flush()
    return updated


async def enrich_course_graph(db: Session, course_id: str, *, run_relation_completion: bool = True) -> dict[str, Any]:
    from app.models import Concept, ConceptRelation, Course

    course = db.get(Course, course_id)
    if course is None:
        raise LookupError(f"Course not found: {course_id}")
    concepts = db.scalars(select(Concept).where(Concept.course_id == course_id)).all()
    relations = db.scalars(select(ConceptRelation).where(ConceptRelation.course_id == course_id)).all()
    if not concepts:
        return {"graph_algorithm_nodes": 0, "graph_algorithm_edges": 0}

    signals = collect_concept_signals(db, course, concepts, relations)
    relation_signals = relation_signals_from_db(relations)
    edges = build_sparse_edges(signals, relation_signals)
    metrics, graph = analyze_graph(signals, edges)
    completion_error: str | None = None
    completion_count = 0
    if run_relation_completion:
        try:
            completion_signals = await complete_relations_with_llm(
                db,
                course_id,
                {concept.id: concept for concept in concepts},
                graph,
                metrics,
            )
        except Exception as exc:
            completion_signals = []
            completion_error = exception_summary(exc)
        completion_count = len(completion_signals)
        if completion_signals:
            relation_signals = [*relation_signals, *completion_signals]
            edges = build_sparse_edges(signals, relation_signals)
            metrics, graph = analyze_graph(signals, edges)

    inferred_edges = infer_dijkstra_edges(graph, signals, edges)
    if inferred_edges:
        edges = [*edges, *inferred_edges]
        metrics, graph = analyze_graph(signals, edges)
    keep_ids = select_concepts_to_keep(signals, metrics, graph)
    edge_updates = upsert_weighted_edges(db, course_id, edges)
    concept_updates = write_concept_metrics(db, concepts, signals, metrics, keep_ids)
    return {
        "graph_algorithm_nodes": len(keep_ids),
        "graph_algorithm_edges": graph.number_of_edges(),
        "graph_algorithm_edge_updates": edge_updates,
        "graph_algorithm_concept_updates": concept_updates,
        "graph_algorithm_inferred_edges": len(inferred_edges),
        "graph_algorithm_components": nx.number_connected_components(graph) if graph.number_of_nodes() else 0,
        "graph_relation_completion_edges": completion_count,
        "graph_relation_completion_error": completion_error,
    }


async def enrich_course_graph_without_completion(db: Session, course_id: str) -> dict[str, Any]:
    return await enrich_course_graph(db, course_id, run_relation_completion=False)
