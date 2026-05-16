import { describe, expect, it } from "vitest";

import { evidenceFirstTraceFallbackSteps, traceAuditSummary, traceNodeLabel } from "./agent-trace";

describe("agent trace helpers", () => {
  it("uses evidence-first fallback steps", () => {
    expect(evidenceFirstTraceFallbackSteps).toEqual([
      "perception",
      "retrieval_planner",
      "base_retrieval",
      "evidence_anchor_selector",
      "evidence_chain_planner",
      "controlled_graph_enhancer",
      "evidence_assembler",
      "document_grader",
      "evidence_evaluator",
      "context_synthesizer",
      "answer_generator",
      "citation_checker",
    ]);
  });

  it("labels new and legacy nodes", () => {
    expect(traceNodeLabel("base_retrieval")).toBe("基础召回");
    expect(traceNodeLabel("evidence_chain_planner")).toBe("证据链规划");
    expect(traceNodeLabel("retrievers")).toBe("基础召回");
  });

  it("summarizes evidence-first and chunk-route audit scores", () => {
    expect(
      traceAuditSummary({
        audit: {
          anchor_count: 2,
          planned_paths: 3,
          verified_edges: 4,
          chunk_retained: 10,
          graph_extraction_eligible_chunks: 6,
        },
      }),
    ).toEqual(["anchors: 2", "paths: 3", "verified: 4", "retained chunks: 10", "graph route: 6"]);
  });
});
