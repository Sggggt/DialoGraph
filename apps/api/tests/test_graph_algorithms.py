from __future__ import annotations

from types import SimpleNamespace


def signal(concept_id: str, evidence_count: int, refs: tuple[str, ...] = ("L1",), vector: tuple[float, ...] | None = None):
    from app.services.graph_algorithms import ConceptSignal

    return ConceptSignal(
        concept_id=concept_id,
        importance=0.7,
        evidence_count=evidence_count,
        chapter_refs=refs,
        vector=vector,
    )


def test_dynamic_rnn_knn_edges_stay_sparse():
    from app.services.graph_algorithms import build_sparse_edges, dynamic_k

    concepts = [
        signal(f"c{index}", index % 6 + 1, vector=(1.0, index / 30, 0.0))
        for index in range(30)
    ]

    edges = build_sparse_edges(concepts, [])

    assert len(edges) <= sum(dynamic_k(concept.evidence_count) for concept in concepts)
    assert len(edges) < 30 * 12
    assert all(0.0 <= edge.weight <= 1.0 for edge in edges)


def test_analyze_graph_assigns_communities_and_centrality():
    from app.services.graph_algorithms import RelationSignal, analyze_graph, build_sparse_edges

    concepts = [
        signal("a", 4, ("L1",), (1.0, 0.0)),
        signal("b", 3, ("L1",), (0.98, 0.02)),
        signal("c", 3, ("L2",), (0.0, 1.0)),
        signal("d", 2, ("L2",), (0.02, 0.98)),
    ]
    relations = [
        RelationSignal("a", "b", "relates_to", 0.9),
        RelationSignal("c", "d", "relates_to", 0.9),
        RelationSignal("b", "c", "prerequisite_of", 0.6),
    ]

    edges = build_sparse_edges(concepts, relations)
    metrics, graph = analyze_graph(concepts, edges)

    assert graph.number_of_edges() >= 3
    assert set(metrics) == {"a", "b", "c", "d"}
    assert all("community_louvain" in item for item in metrics.values())
    assert all("centrality_score" in item["centrality"] for item in metrics.values())


def test_dijkstra_infers_hidden_relation_and_marks_source():
    from app.services.graph_algorithms import WeightedEdge, analyze_graph, infer_dijkstra_edges

    concepts = [
        signal("a", 6, ("L1",), (1.0, 0.0, 0.0)),
        signal("b", 6, ("L1",), (0.96, 0.04, 0.0)),
        signal("c", 6, ("L1",), (0.93, 0.07, 0.0)),
    ]
    edges = [
        WeightedEdge("a", "b", "relates_to", 0.85, 0.9, 2, 0.9, None, "llm", False, {}),
        WeightedEdge("b", "c", "relates_to", 0.85, 0.9, 2, 0.9, None, "llm", False, {}),
    ]
    _metrics, graph = analyze_graph(concepts, edges)

    inferred = infer_dijkstra_edges(graph, concepts, edges)

    assert inferred
    assert inferred[0].relation_source == "dijkstra_inferred"
    assert inferred[0].is_inferred is True
    assert inferred[0].metadata["path"] == ["a", "b", "c"]


def test_pruning_keeps_minimum_available_nodes():
    from app.services.graph_algorithms import analyze_graph, select_concepts_to_keep

    concepts = [signal(f"n{index}", 1, (), (1.0, 0.0)) for index in range(12)]
    metrics, graph = analyze_graph(concepts, [])

    keep = select_concepts_to_keep(concepts, metrics, graph)

    assert len(keep) == 12


def test_completion_relation_signals_ignores_bad_concept_shape():
    from app.services.graph_algorithms import completion_relation_signals

    degree = SimpleNamespace(id="c1", canonical_name="Degree Centrality")
    closeness = SimpleNamespace(id="c2", canonical_name="Closeness Centrality")
    signals = completion_relation_signals(
        {
            "concepts": ["Degree Centrality", "Closeness Centrality"],
            "relations": [
                {
                    "source": "Degree Centrality",
                    "target": "Closeness Centrality",
                    "relation_type": "compares",
                    "confidence": "0.82",
                }
            ],
        },
        {degree.canonical_name: degree, closeness.canonical_name: closeness},
    )

    assert len(signals) == 1
    assert signals[0].source_id == "c1"
    assert signals[0].target_id == "c2"
    assert signals[0].confidence == 0.82
