export type Visibility = "private";

export type SourceType =
  | "pdf"
  | "ppt"
  | "pptx"
  | "docx"
  | "markdown"
  | "text"
  | "image"
  | "notebook"
  | "html"
  | "unknown";

export type JobState =
  | "queued"
  | "parsing"
  | "chunking"
  | "embedding"
  | "extracting_graph"
  | "processing"
  | "completed"
  | "partial_failed"
  | "failed"
  | "skipped";

export type CourseFileStatus = "pending" | "parsing" | "parsed" | "failed" | "skipped";

export type AgentRoute =
  | "direct_answer"
  | "retrieve_notes"
  | "retrieve_exercises"
  | "retrieve_both"
  | "clarify"
  | "multi_hop_research";

export type AgentRunState = "queued" | "running" | "needs_clarification" | "completed" | "failed";

export interface SearchFilters {
  chapter?: string;
  tags?: string[];
  difficulty?: string;
  source_type?: SourceType;
}

export interface UploadFileResponse {
  document_id: string;
  job_id?: string | null;
  status: JobState;
  source_path: string;
}

export interface JobStatusResponse {
  job_id: string;
  state: JobState;
  error?: string | null;
  document_id?: string | null;
  source_path?: string | null;
  batch_id?: string | null;
  stats?: Record<string, unknown>;
}

export interface SearchRequest {
  query: string;
  course_id?: string | null;
  filters?: SearchFilters;
  top_k?: number;
}

export interface Citation {
  chunk_id: string;
  document_id: string;
  document_title: string;
  source_path: string;
  chapter?: string | null;
  section?: string | null;
  page_number?: number | null;
  snippet: string;
}

export interface SearchResult {
  chunk_id: string;
  snippet: string;
  score: number;
  citations: Citation[];
  metadata: Record<string, unknown>;
  content?: string | null;
  child_content?: string | null;
  document_title?: string | null;
  source_path?: string | null;
  chapter?: string | null;
  source_type?: string | null;
}

export interface ModelAudit {
  embedding_provider: string;
  embedding_model?: string | null;
  embedding_external_called: boolean;
  embedding_fallback_reason?: string | null;
  reranker_enabled: boolean;
  reranker_called: boolean;
  fallback_enabled: boolean;
  degraded_mode: boolean;
  vector_index_warning?: string | null;
}

export interface AnswerModelAudit {
  provider: string;
  model?: string | null;
  external_called: boolean;
  fallback_reason?: string | null;
  skipped_reason?: string | null;
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
  degraded_mode: boolean;
  model_audit: ModelAudit;
}

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface QARequest {
  question: string;
  session_id?: string | null;
  course_id?: string | null;
  filters?: SearchFilters;
  top_k?: number;
  history?: ChatMessage[];
}

export interface QAResponse {
  run_id?: string | null;
  session_id?: string | null;
  answer: string;
  citations: Citation[];
  used_chunks: Array<Record<string, unknown>>;
  route?: AgentRoute | null;
  trace?: AgentTraceEventPayload[];
  degraded_mode: boolean;
  answer_model_audit: AnswerModelAudit;
}

export interface AgentRequest {
  question: string;
  session_id?: string | null;
  course_id?: string | null;
  filters?: SearchFilters;
  top_k?: number;
  history?: ChatMessage[];
  stream_trace?: boolean;
}

export interface AgentTraceEventPayload {
  id?: string | null;
  run_id?: string | null;
  node: string;
  status: string;
  input_summary?: string | null;
  output_summary?: string | null;
  document_ids: string[];
  scores: Record<string, unknown>;
  duration_ms: number;
  error?: string | null;
  created_at?: string | null;
}

export interface AgentResponse {
  run_id: string;
  session_id: string;
  answer: string;
  citations: Citation[];
  used_chunks: Array<Record<string, unknown>>;
  route: AgentRoute;
  trace: AgentTraceEventPayload[];
  degraded_mode: boolean;
  answer_model_audit: AnswerModelAudit;
}

export interface TaskStatusResponse {
  run_id: string;
  state: AgentRunState;
  current_node?: string | null;
  retry_count: number;
  route?: AgentRoute | null;
  error?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface SessionSummary {
  id: string;
  title?: string | null;
  last_question?: string | null;
  last_answer?: string | null;
  transcript: Array<Record<string, unknown>>;
  created_at: string;
  updated_at: string;
}

export interface SessionMessagesResponse {
  session_id: string;
  messages: Array<Record<string, unknown>>;
}

export interface DeleteResponse {
  deleted: boolean;
}

export interface CleanupStaleDataResponse {
  deleted_vectors: number;
  deleted_chunks: number;
  deleted_document_versions: number;
  deleted_documents: number;
  removed_graph_relations: number;
  removed_graph_concepts: number;
}

export interface CleanupStaleGraphResponse {
  removed_relations: number;
  removed_aliases: number;
  removed_concepts: number;
}

export interface RebuildGraphRequest {
  mode: "incremental" | "full";
}

export interface RebuildGraphResponse {
  batch_id: string;
  state: string;
  mode: string;
  affected_documents: number;
  previous_batch_id?: string | null;
}

export interface BatchLogTokenResponse {
  token: string;
  expires_at: string;
}

export interface DeleteCourseResponse {
  deleted: boolean;
  deleted_vectors: number;
  deleted_trace_events: number;
  deleted_agent_runs: number;
  deleted_sessions: number;
  deleted_ingestion_logs: number;
  deleted_compensations: number;
  deleted_jobs: number;
  deleted_batches: number;
  deleted_relations: number;
  deleted_aliases: number;
  deleted_concepts: number;
  deleted_chunks: number;
  deleted_document_versions: number;
  deleted_documents: number;
  deleted_courses: number;
  deleted_directory: number;
}

export interface RefreshResponse {
  course_id: string;
  refreshed_at: string;
}

export interface ModelSettingsResponse {
  provider: "openai_compatible";
  base_url: string;
  resolve_ip?: string | null;
  embedding_model: string;
  chat_model: string;
  embedding_dimensions: number;
  graph_extraction_chunk_limit: number;
  graph_extraction_chunks_per_document: number;
  reranker_enabled: boolean;
  reranker_model: string;
  reranker_max_length: number;
  reranker_device: "cpu" | "cuda";
  reranker_url: string;
  semantic_chunking_enabled: boolean;
  semantic_chunking_min_length: number;
  model_bridge_enabled: boolean;
  has_api_key: boolean;
  degraded_mode: boolean;
}

export interface ModelSettingsUpdate {
  api_key?: string | null;
  clear_api_key?: boolean;
  base_url?: string | null;
  resolve_ip?: string | null;
  embedding_model?: string | null;
  chat_model?: string | null;
  embedding_dimensions?: number | null;
  graph_extraction_chunk_limit?: number | null;
  graph_extraction_chunks_per_document?: number | null;
  reranker_enabled?: boolean | null;
  reranker_model?: string | null;
  reranker_max_length?: number | null;
  reranker_device?: "cpu" | "cuda" | null;
  semantic_chunking_enabled?: boolean | null;
  semantic_chunking_min_length?: number | null;
  model_bridge_enabled?: boolean | null;
}

export interface RuntimeIssue {
  code: string;
  title: string;
  message: string;
  fix_commands: string[];
}

export interface EnvSyncStatus {
  synced: boolean;
  missing_keys: string[];
  extra_keys: string[];
  bom_keys: string[];
}

export interface RerankerRuntimeStatus {
  enabled: boolean;
  device: string;
  model: string;
  url: string;
  reachable: boolean;
  healthy: boolean;
  reported_model?: string | null;
  reported_device?: string | null;
  model_matches?: boolean | null;
  device_matches?: boolean | null;
}

export interface InfrastructureStatus {
  postgres: boolean;
  qdrant: boolean;
  redis: boolean;
  model_bridge?: boolean | null;
}

export interface RuntimeCheckResponse {
  env_sync: EnvSyncStatus;
  reranker: RerankerRuntimeStatus;
  infrastructure: InfrastructureStatus;
  blocking_issues: RuntimeIssue[];
  warnings: RuntimeIssue[];
}

export interface StructuredApiErrorBody {
  code: string;
  title: string;
  message: string;
  issues: RuntimeIssue[];
  fix_commands: string[];
}

export interface RelatedConcept {
  concept_id: string;
  relation_type: string;
  target_name: string;
  confidence?: number | null;
  weight?: number | null;
  relation_source?: string | null;
  is_inferred?: boolean;
}

export interface ConceptCard {
  concept_id: string;
  name: string;
  aliases: string[];
  summary: string;
  chapter_refs: string[];
  concept_type: string;
  importance_score: number;
  related_concepts: RelatedConcept[];
}

export interface GraphNode {
  id: string;
  name: string;
  category: string;
  value?: number;
  chapter?: string | null;
  importance_score?: number | null;
  source_type?: string | null;
  evidence_count?: number | null;
  community_louvain?: number | null;
  community_spectral?: number | null;
  component_id?: number | null;
  centrality_score?: number | null;
  graph_rank_score?: number | null;
}

export interface GraphEdge {
  source: string;
  target: string;
  label: string;
  confidence?: number | null;
  category?: string | null;
  evidence_chunk_id?: string | null;
  weight?: number | null;
  semantic_similarity?: number | null;
  support_count?: number | null;
  relation_source?: string | null;
  is_inferred?: boolean;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  focus_chapter?: string | null;
}

export interface CourseTreeNode {
  id: string;
  title: string;
  type: "course" | "chapter" | "document" | "concept";
  children?: CourseTreeNode[];
}

export interface CourseSummary {
  id: string;
  name: string;
  description?: string | null;
  source_root: string;
  storage_root: string;
  document_count: number;
  concept_count: number;
  degraded_mode: boolean;
}

export interface CourseCreateRequest {
  name: string;
  description?: string | null;
}

export interface BatchError {
  source_path: string;
  message: string;
}

  export interface IngestionBatchSummary {
    batch_id: string;
    state: JobState;
    trigger_source: string;
    source_root: string;
  total_files: number;
  processed_files: number;
  success_count: number;
  failure_count: number;
    skipped_count: number;
    coverage_by_source_type: Record<string, number>;
    errors: BatchError[];
    graph_stats: Record<string, unknown>;
    started_at?: string | null;
    completed_at?: string | null;
  }

export interface BatchStartResponse {
  batch_id: string;
  state: JobState;
}

export interface ParseUploadedFilesRequest {
  file_paths: string[];
  force?: boolean;
}

export interface DashboardSnapshot {
  course: CourseSummary;
  tree: CourseTreeNode[];
  graph: GraphResponse;
  batch_status?: IngestionBatchSummary | null;
  ingested_document_count: number;
  graph_relation_count: number;
  coverage_by_source_type: Record<string, number>;
  degraded_mode: boolean;
}

export interface CourseFileSummary {
  id: string;
  document_id?: string | null;
  title: string;
  source_path: string;
  source_type: string;
  chapter?: string | null;
  status: CourseFileStatus;
  job_state?: JobState | null;
  batch_id?: string | null;
  error?: string | null;
  chunk_count: number;
  updated_at?: string | null;
}

export interface GraphNodeRelation {
  relation_id: string;
  relation_type: string;
  target_concept_id?: string | null;
  target_name: string;
  confidence: number;
  weight?: number | null;
  semantic_similarity?: number | null;
  support_count?: number | null;
  relation_source?: string | null;
  is_inferred?: boolean;
  evidence?: Citation | null;
}

export interface GraphNodeDetail {
  concept_id: string;
  name: string;
  normalized_name: string;
  summary: string;
  aliases: string[];
  chapter_refs: string[];
  concept_type: string;
  importance_score: number;
  evidence_count: number;
  community_louvain?: number | null;
  community_spectral?: number | null;
  component_id?: number | null;
  centrality: Record<string, number>;
  graph_rank_score: number;
  relations: GraphNodeRelation[];
}
