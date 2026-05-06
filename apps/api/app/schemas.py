from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


JobState = Literal[
    "queued",
    "parsing",
    "chunking",
    "embedding",
    "extracting_graph",
    "processing",
    "completed",
    "partial_failed",
    "failed",
    "skipped",
]
CourseFileStatus = Literal["pending", "parsing", "parsed", "failed", "skipped"]
SourceType = Literal["pdf", "ppt", "pptx", "docx", "markdown", "text", "image", "notebook", "html", "unknown"]
AgentRoute = Literal["direct_answer", "retrieve_notes", "retrieve_exercises", "retrieve_both", "clarify", "multi_hop_research"]
AgentRunState = Literal["queued", "running", "needs_clarification", "completed", "failed"]


class SearchFilters(BaseModel):
    chapter: str | None = None
    tags: list[str] = Field(default_factory=list)
    difficulty: str | None = None
    source_type: SourceType | None = None


class UploadFileResponse(BaseModel):
    document_id: str
    job_id: str | None = None
    status: JobState
    source_path: str


class ParseUploadedFilesRequest(BaseModel):
    file_paths: list[str] = Field(default_factory=list)
    force: bool = False


class JobStatusResponse(BaseModel):
    job_id: str
    state: JobState
    error: str | None = None
    document_id: str | None = None
    source_path: str | None = None
    batch_id: str | None = None
    stats: dict = Field(default_factory=dict)


class Citation(BaseModel):
    chunk_id: str
    document_id: str
    document_title: str
    source_path: str
    chapter: str | None = None
    section: str | None = None
    page_number: int | None = None
    snippet: str


class SearchRequest(BaseModel):
    query: str
    course_id: str | None = None
    filters: SearchFilters = Field(default_factory=SearchFilters)
    top_k: int = Field(default=6, ge=1, le=50)


class SearchResult(BaseModel):
    chunk_id: str
    snippet: str
    score: float
    citations: list[Citation]
    metadata: dict
    content: str | None = None
    document_title: str | None = None
    source_path: str | None = None
    chapter: str | None = None
    source_type: str | None = None


class ModelAudit(BaseModel):
    embedding_provider: str = "none"
    embedding_model: str | None = None
    embedding_external_called: bool = False
    embedding_fallback_reason: str | None = None
    reranker_enabled: bool = False
    reranker_called: bool = False
    fallback_enabled: bool = False
    degraded_mode: bool = False
    vector_index_warning: str | None = None


class AnswerModelAudit(BaseModel):
    provider: str = "none"
    model: str | None = None
    external_called: bool = False
    fallback_reason: str | None = None
    skipped_reason: str | None = None


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
    degraded_mode: bool = False
    model_audit: ModelAudit = Field(default_factory=ModelAudit)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class QARequest(BaseModel):
    question: str
    session_id: str | None = None
    course_id: str | None = None
    filters: SearchFilters = Field(default_factory=SearchFilters)
    top_k: int = Field(default=6, ge=1, le=50)
    history: list[ChatMessage] = Field(default_factory=list)


class QAResponse(BaseModel):
    run_id: str | None = None
    session_id: str | None = None
    answer: str
    citations: list[Citation]
    used_chunks: list[dict]
    route: AgentRoute | None = None
    trace: list["AgentTraceEventPayload"] = Field(default_factory=list)
    degraded_mode: bool = False
    answer_model_audit: AnswerModelAudit = Field(default_factory=AnswerModelAudit)


class AgentRequest(BaseModel):
    question: str
    session_id: str | None = None
    course_id: str | None = None
    filters: SearchFilters = Field(default_factory=SearchFilters)
    top_k: int = Field(default=6, ge=1, le=50)
    history: list[ChatMessage] = Field(default_factory=list)
    stream_trace: bool = True


class AgentTraceEventPayload(BaseModel):
    id: str | None = None
    run_id: str | None = None
    node: str
    status: str = "completed"
    input_summary: str | None = None
    output_summary: str | None = None
    document_ids: list[str] = Field(default_factory=list)
    scores: dict = Field(default_factory=dict)
    duration_ms: int = 0
    error: str | None = None
    created_at: datetime | None = None


class AgentResponse(BaseModel):
    run_id: str
    session_id: str
    answer: str
    citations: list[Citation]
    used_chunks: list[dict]
    route: AgentRoute
    trace: list[AgentTraceEventPayload]
    degraded_mode: bool = False
    answer_model_audit: AnswerModelAudit = Field(default_factory=AnswerModelAudit)


class TaskStatusResponse(BaseModel):
    run_id: str
    state: AgentRunState
    current_node: str | None = None
    retry_count: int = 0
    route: AgentRoute | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class SessionSummary(BaseModel):
    id: str
    title: str | None = None
    last_question: str | None = None
    last_answer: str | None = None
    transcript: list[dict] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class SessionMessagesResponse(BaseModel):
    session_id: str
    messages: list[dict] = Field(default_factory=list)


class DeleteResponse(BaseModel):
    deleted: bool


class CleanupStaleDataResponse(BaseModel):
    deleted_vectors: int = 0
    deleted_chunks: int = 0
    deleted_document_versions: int = 0
    deleted_documents: int = 0
    removed_graph_relations: int = 0
    removed_graph_concepts: int = 0


class CleanupStaleGraphResponse(BaseModel):
    removed_relations: int = 0
    removed_aliases: int = 0
    removed_concepts: int = 0


class RebuildGraphResponse(BaseModel):
    graph_rebuilt: bool
    concepts: int
    relations: int
    graph_nodes: int
    graph_edges: int
    graph_extraction_provider: str
    graph_extraction_chunk_limit: int
    graph_extraction_chunks_per_document: int
    graph_llm_selected_chunks: int
    graph_llm_source_documents: int
    graph_llm_success_chunks: int
    graph_llm_failed_chunks: int
    graph_total_active_chunks: int
    graph_source_documents: int


class DeleteCourseResponse(BaseModel):
    deleted: bool
    deleted_vectors: int = 0
    deleted_trace_events: int = 0
    deleted_agent_runs: int = 0
    deleted_sessions: int = 0
    deleted_ingestion_logs: int = 0
    deleted_compensations: int = 0
    deleted_jobs: int = 0
    deleted_batches: int = 0
    deleted_relations: int = 0
    deleted_aliases: int = 0
    deleted_concepts: int = 0
    deleted_chunks: int = 0
    deleted_document_versions: int = 0
    deleted_documents: int = 0
    deleted_courses: int = 0
    deleted_directory: int = 0


class RefreshResponse(BaseModel):
    course_id: str
    refreshed_at: datetime


class ModelSettingsResponse(BaseModel):
    provider: Literal["openai_compatible"] = "openai_compatible"
    base_url: str
    model_bridge_enabled: bool = False
    resolve_ip: str | None = None
    embedding_model: str
    chat_model: str
    embedding_dimensions: int
    graph_extraction_chunk_limit: int
    graph_extraction_chunks_per_document: int
    reranker_enabled: bool = False
    reranker_model: str = ""
    reranker_max_length: int = 512
    reranker_device: str = "cpu"
    reranker_url: str = ""
    semantic_chunking_enabled: bool = True
    semantic_chunking_min_length: int = 2000
    has_api_key: bool
    degraded_mode: bool


class ModelSettingsUpdate(BaseModel):
    api_key: str | None = None
    clear_api_key: bool = False
    base_url: str | None = None
    model_bridge_enabled: bool | None = None
    resolve_ip: str | None = None
    embedding_model: str | None = None
    chat_model: str | None = None
    embedding_dimensions: int | None = Field(default=None, ge=1, le=8192)
    graph_extraction_chunk_limit: int | None = Field(default=None, ge=1, le=200)
    graph_extraction_chunks_per_document: int | None = Field(default=None, ge=1, le=10)
    reranker_enabled: bool | None = None
    reranker_model: str | None = None
    reranker_max_length: int | None = Field(default=None, ge=64, le=2048)
    reranker_device: str | None = None
    semantic_chunking_enabled: bool | None = None
    semantic_chunking_min_length: int | None = Field(default=None, ge=500, le=5000)


class RuntimeIssue(BaseModel):
    code: str
    title: str
    message: str
    fix_commands: list[str] = Field(default_factory=list)


class EnvSyncStatus(BaseModel):
    synced: bool
    missing_keys: list[str] = Field(default_factory=list)
    extra_keys: list[str] = Field(default_factory=list)
    bom_keys: list[str] = Field(default_factory=list)


class InfrastructureStatus(BaseModel):
    postgres: bool
    qdrant: bool
    redis: bool
    model_bridge: bool | None = None


class RuntimeCheckResponse(BaseModel):
    env_sync: EnvSyncStatus
    reranker: dict = Field(default_factory=dict)
    infrastructure: InfrastructureStatus
    blocking_issues: list[RuntimeIssue] = Field(default_factory=list)
    warnings: list[RuntimeIssue] = Field(default_factory=list)


class StructuredApiError(BaseModel):
    code: str
    title: str
    message: str
    issues: list[RuntimeIssue] = Field(default_factory=list)
    fix_commands: list[str] = Field(default_factory=list)


class RelatedConcept(BaseModel):
    concept_id: str
    relation_type: str
    target_name: str
    confidence: float | None = None


class ConceptCard(BaseModel):
    concept_id: str
    name: str
    aliases: list[str]
    summary: str
    chapter_refs: list[str]
    concept_type: str = "concept"
    importance_score: float = 0.0
    related_concepts: list[RelatedConcept]


class GraphNode(BaseModel):
    id: str
    name: str
    category: str
    value: int | float | None = None
    chapter: str | None = None
    importance_score: float | None = None
    source_type: str | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    label: str
    confidence: float | None = None
    category: str | None = None
    evidence_chunk_id: str | None = None


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    focus_chapter: str | None = None


class CourseTreeNode(BaseModel):
    id: str
    title: str
    type: Literal["course", "chapter", "document", "concept"]
    children: list["CourseTreeNode"] = Field(default_factory=list)


class CourseSummary(BaseModel):
    id: str
    name: str
    description: str | None = None
    source_root: str
    storage_root: str
    document_count: int
    concept_count: int
    degraded_mode: bool = False


class CourseCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class IngestionStats(BaseModel):
    chunks: int = 0
    concepts: int = 0
    relations: int = 0


class BatchError(BaseModel):
    source_path: str
    message: str


class IngestionBatchSummary(BaseModel):
    batch_id: str
    state: JobState
    trigger_source: str
    source_root: str
    total_files: int
    processed_files: int
    success_count: int
    failure_count: int
    skipped_count: int
    coverage_by_source_type: dict[str, int] = Field(default_factory=dict)
    errors: list[BatchError] = Field(default_factory=list)
    graph_stats: dict = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class BatchStartResponse(BaseModel):
    batch_id: str
    state: JobState


class DashboardSnapshot(BaseModel):
    course: CourseSummary
    tree: list[CourseTreeNode]
    graph: GraphResponse
    batch_status: IngestionBatchSummary | None = None
    ingested_document_count: int = 0
    graph_relation_count: int = 0
    coverage_by_source_type: dict[str, int] = Field(default_factory=dict)
    degraded_mode: bool = False


class ChunkPayload(BaseModel):
    id: str
    document_id: str
    document_title: str
    source_path: str
    source_type: str
    chapter: str | None = None
    section: str | None = None
    page_number: int | None = None
    snippet: str
    content: str
    metadata: dict = Field(default_factory=dict)


class DocumentSummary(BaseModel):
    id: str
    title: str
    source_path: str
    source_type: str
    chapter: str | None = None
    updated_at: datetime


class CourseFileSummary(BaseModel):
    id: str
    document_id: str | None = None
    title: str
    source_path: str
    source_type: str = "unknown"
    chapter: str | None = None
    status: CourseFileStatus
    job_state: JobState | None = None
    batch_id: str | None = None
    error: str | None = None
    chunk_count: int = 0
    updated_at: datetime | None = None


class GraphNodeRelation(BaseModel):
    relation_id: str
    relation_type: str
    target_concept_id: str | None = None
    target_name: str
    confidence: float
    evidence: Citation | None = None


class GraphNodeDetail(BaseModel):
    concept_id: str
    name: str
    normalized_name: str
    summary: str
    aliases: list[str]
    chapter_refs: list[str]
    concept_type: str
    importance_score: float
    relations: list[GraphNodeRelation]


CourseTreeNode.model_rebuild()
QAResponse.model_rebuild()
