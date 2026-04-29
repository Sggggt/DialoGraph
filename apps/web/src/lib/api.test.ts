import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("api client", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.test/api");
    vi.stubEnv("NEXT_PUBLIC_API_KEY", "test-key");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("sends API key headers on JSON requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ has_api_key: true }));
    vi.stubGlobal("fetch", fetchMock);
    const { updateModelSettings } = await import("./api");

    await updateModelSettings({ api_key: "new-key", clear_api_key: false });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.test/api/settings/model",
      expect.objectContaining({
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-API-Key": "test-key" },
      }),
    );
  });

  it("adds API key to batch log URLs for EventSource", async () => {
    const { getBatchLogUrl } = await import("./api");

    expect(getBatchLogUrl("batch-1")).toBe("http://api.test/api/ingestion/batches/batch-1/logs?api_key=test-key");
  });

  it("calls stale cleanup endpoints with API key headers", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ deleted_vectors: 1, deleted_chunks: 2, deleted_document_versions: 3, deleted_documents: 4, removed_graph_relations: 5, removed_graph_concepts: 6 }))
      .mockResolvedValueOnce(jsonResponse({ removed_relations: 1, removed_aliases: 2, removed_concepts: 3 }));
    vi.stubGlobal("fetch", fetchMock);
    const { cleanupStaleData, cleanupStaleGraph } = await import("./api");

    await cleanupStaleData("course-1");
    await cleanupStaleGraph("course-1");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://api.test/api/maintenance/cleanup-stale-data?course_id=course-1",
      expect.objectContaining({ method: "POST", headers: { "X-API-Key": "test-key" } }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://api.test/api/maintenance/cleanup-stale-graph?course_id=course-1",
      expect.objectContaining({ method: "POST", headers: { "X-API-Key": "test-key" } }),
    );
  });

  it("deletes courses with API key headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ deleted: true }));
    vi.stubGlobal("fetch", fetchMock);
    const { deleteCourse } = await import("./api");

    await deleteCourse("course-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.test/api/courses/course-1",
      expect.objectContaining({ method: "DELETE", headers: { "X-API-Key": "test-key" } }),
    );
  });

  it("parses SSE stream chunks", async () => {
    const body = new ReadableStream({
      start(controller) {
        const encoder = new TextEncoder();
        controller.enqueue(encoder.encode('data: {"type":"meta","run_id":"run-1","session_id":"session-1"}\n\n'));
        controller.enqueue(encoder.encode('data: {"token":"hello"}\n\n'));
        controller.enqueue(encoder.encode('data: {"type":"final","response":{"run_id":"run-1","session_id":"session-1","answer":"done","citations":[],"used_chunks":[],"route":"retrieve_notes","trace":[],"degraded_mode":false}}\n\n'));
        controller.enqueue(encoder.encode("data: [DONE]\n\n"));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status: 200 })));
    const { streamAnswer } = await import("./api");
    const tokens: string[] = [];
    const meta: unknown[] = [];

    await streamAnswer(
      { question: "hello", top_k: 3 },
      {
        onToken: (value) => tokens.push(value),
        onCitations: () => undefined,
        onMeta: (value) => meta.push(value),
      },
    );

    expect(tokens).toEqual(["hello"]);
    expect(meta).toContainEqual({ run_id: "run-1", session_id: "session-1", route: undefined });
    expect(meta).toContainEqual({ degraded_mode: false, run_id: "run-1", session_id: "session-1", route: "retrieve_notes" });
  });
});
