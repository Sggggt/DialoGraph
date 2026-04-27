import type {
  AgentResponse,
  AgentTraceEventPayload,
  BatchStartResponse,
  ConceptCard,
  CourseFileSummary,
  CourseCreateRequest,
  CourseSummary,
  DashboardSnapshot,
  DeleteResponse,
  GraphNodeDetail,
  GraphResponse,
  IngestionBatchSummary,
  JobStatusResponse,
  ModelSettingsResponse,
  ModelSettingsUpdate,
  ParseUploadedFilesRequest,
  QARequest,
  QAResponse,
  RefreshResponse,
  SearchRequest,
  SearchResponse,
  SessionMessagesResponse,
  SessionSummary,
  TaskStatusResponse,
  UploadFileResponse,
} from "@course-kg/shared";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api";

function buildApiUrl(path: string, params?: Record<string, string | null | undefined>): string {
  const url = new URL(`${API_BASE_URL}${path}`);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value) {
        url.searchParams.set(key, value);
      }
    }
  }
  return url.toString();
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function fetchCourses(): Promise<CourseSummary[]> {
  const response = await fetch(buildApiUrl("/courses"), { cache: "no-store" });
  return parseResponse<CourseSummary[]>(response);
}

export async function createCourse(payload: CourseCreateRequest): Promise<CourseSummary> {
  const response = await fetch(buildApiUrl("/courses"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseResponse<CourseSummary>(response);
}

export async function fetchDashboard(courseId?: string | null): Promise<DashboardSnapshot> {
  const response = await fetch(buildApiUrl("/courses/current/dashboard", { course_id: courseId }), { cache: "no-store" });
  return parseResponse<DashboardSnapshot>(response);
}

export async function refreshCourse(courseId?: string | null): Promise<RefreshResponse> {
  const response = await fetch(buildApiUrl("/courses/current/refresh", { course_id: courseId }), {
    method: "POST",
  });
  return parseResponse<RefreshResponse>(response);
}

export async function fetchModelSettings(): Promise<ModelSettingsResponse> {
  const response = await fetch(buildApiUrl("/settings/model"), { cache: "no-store" });
  return parseResponse<ModelSettingsResponse>(response);
}

export async function updateModelSettings(payload: ModelSettingsUpdate): Promise<ModelSettingsResponse> {
  const response = await fetch(buildApiUrl("/settings/model"), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseResponse<ModelSettingsResponse>(response);
}

export async function fetchCourseFiles(courseId?: string | null): Promise<CourseFileSummary[]> {
  const response = await fetch(buildApiUrl("/course-files", { course_id: courseId }), { cache: "no-store" });
  return parseResponse<CourseFileSummary[]>(response);
}

export async function removeCourseFile(sourcePath: string, courseId?: string | null): Promise<{ removed: boolean }> {
  const response = await fetch(buildApiUrl("/course-files", { course_id: courseId, source_path: sourcePath }), {
    method: "DELETE",
  });
  return parseResponse<{ removed: boolean }>(response);
}

export async function fetchGraph(courseId?: string | null): Promise<GraphResponse> {
  const response = await fetch(buildApiUrl("/courses/current/graph", { course_id: courseId }), { cache: "no-store" });
  return parseResponse<GraphResponse>(response);
}

export async function fetchChapterGraph(chapter: string, courseId?: string | null): Promise<GraphResponse> {
  const response = await fetch(buildApiUrl(`/graph/chapters/${encodeURIComponent(chapter)}`, { course_id: courseId }), { cache: "no-store" });
  return parseResponse<GraphResponse>(response);
}

export async function fetchGraphNode(conceptId: string, courseId?: string | null): Promise<GraphNodeDetail> {
  const response = await fetch(buildApiUrl(`/graph/nodes/${conceptId}`, { course_id: courseId }), { cache: "no-store" });
  return parseResponse<GraphNodeDetail>(response);
}

export async function fetchConcepts(courseId?: string | null): Promise<ConceptCard[]> {
  const response = await fetch(buildApiUrl("/concepts", { course_id: courseId }), { cache: "no-store" });
  return parseResponse<ConceptCard[]>(response);
}

export async function searchKnowledge(payload: SearchRequest): Promise<SearchResponse> {
  const response = await fetch(`${API_BASE_URL}/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseResponse<SearchResponse>(response);
}

export async function askQuestion(payload: QARequest): Promise<QAResponse> {
  const response = await fetch(`${API_BASE_URL}/qa`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseResponse<QAResponse>(response);
}

export async function callAgent(payload: QARequest): Promise<AgentResponse> {
  const response = await fetch(`${API_BASE_URL}/agent`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseResponse<AgentResponse>(response);
}

export async function uploadFile(file: File, courseId?: string | null): Promise<UploadFileResponse> {
  const formData = new FormData();
  formData.append("upload", file);
  const response = await fetch(buildApiUrl("/files/upload", { course_id: courseId }), {
    method: "POST",
    body: formData,
  });
  return parseResponse<UploadFileResponse>(response);
}

export async function parseUploadedFiles(filePaths: string[], courseId?: string | null, force = false): Promise<BatchStartResponse> {
  const payload: ParseUploadedFilesRequest = { file_paths: filePaths, force };
  const response = await fetch(buildApiUrl("/ingestion/parse-uploaded-files", { course_id: courseId }), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseResponse<BatchStartResponse>(response);
}

export function getBatchLogUrl(batchId: string): string {
  return buildApiUrl(`/ingestion/batches/${batchId}/logs`);
}

export async function fetchJobStatus(jobId: string): Promise<JobStatusResponse> {
  const response = await fetch(`${API_BASE_URL}/jobs/${jobId}`, { cache: "no-store" });
  return parseResponse<JobStatusResponse>(response);
}

export async function fetchBatchStatus(batchId: string): Promise<IngestionBatchSummary> {
  const response = await fetch(`${API_BASE_URL}/ingestion/batches/${batchId}`, { cache: "no-store" });
  return parseResponse<IngestionBatchSummary>(response);
}

export async function fetchTaskStatus(runId: string): Promise<TaskStatusResponse> {
  const response = await fetch(`${API_BASE_URL}/tasks/${runId}`, { cache: "no-store" });
  return parseResponse<TaskStatusResponse>(response);
}

export async function fetchSessions(courseId?: string | null): Promise<SessionSummary[]> {
  const response = await fetch(buildApiUrl("/sessions", { course_id: courseId }), { cache: "no-store" });
  return parseResponse<SessionSummary[]>(response);
}

export async function fetchSessionMessages(sessionId: string): Promise<SessionMessagesResponse> {
  const response = await fetch(`${API_BASE_URL}/sessions/${sessionId}/messages`, { cache: "no-store" });
  return parseResponse<SessionMessagesResponse>(response);
}

export async function deleteSession(sessionId: string): Promise<DeleteResponse> {
  const response = await fetch(`${API_BASE_URL}/sessions/${sessionId}`, { method: "DELETE" });
  return parseResponse<DeleteResponse>(response);
}

export async function streamAnswer(
  payload: QARequest,
  handlers: {
    onToken: (value: string) => void;
    onCitations: (value: QAResponse["citations"]) => void;
    onTrace?: (value: AgentTraceEventPayload) => void;
    onFinal?: (value: AgentResponse) => void;
    onMeta?: (value: { degraded_mode?: boolean; run_id?: string; session_id?: string; route?: string }) => void;
    onError?: (value: string) => void;
  },
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/qa/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok || !response.body) {
    throw new Error("Streaming response unavailable");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) {
      const line = event.replace(/^data:\s*/m, "").trim();
      if (!line || line === "[DONE]") {
        continue;
      }
      const parsed = JSON.parse(line) as {
        type?: string;
        token?: string;
        trace?: AgentTraceEventPayload;
        citations?: QAResponse["citations"];
        degraded_mode?: boolean;
        response?: AgentResponse;
        error?: string;
      };
      if (parsed.type === "trace" && parsed.trace) {
        handlers.onTrace?.(parsed.trace);
      }
      if (parsed.type === "error" && parsed.error) {
        handlers.onError?.(parsed.error);
      }
      if (parsed.token) {
        handlers.onToken(parsed.token);
      }
      if (parsed.citations) {
        handlers.onCitations(parsed.citations);
      }
      if (parsed.type === "final" && parsed.response) {
        handlers.onFinal?.(parsed.response);
        handlers.onMeta?.({
          degraded_mode: parsed.response.degraded_mode,
          run_id: parsed.response.run_id,
          session_id: parsed.response.session_id,
          route: parsed.response.route,
        });
      }
      if (typeof parsed.degraded_mode === "boolean") {
        handlers.onMeta?.({ degraded_mode: parsed.degraded_mode });
      }
    }
  }
}
