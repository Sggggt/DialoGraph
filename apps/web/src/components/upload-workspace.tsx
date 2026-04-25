"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import type { CourseFileStatus, CourseFileSummary } from "@course-kg/shared";
import { AlertCircle, CheckCircle2, Clock3, FileCheck2, Files, LoaderCircle, PanelRightOpen, RefreshCcw, Trash2, UploadCloud, X } from "lucide-react";

import { fetchBatchStatus, fetchCourseFiles, fetchDashboard, getBatchLogUrl, parseUploadedFiles, removeCourseFile, uploadFile } from "@/lib/api";
import { useCourseContext } from "@/components/course-context";
import { ErrorBlock, LoadingBlock } from "@/components/query-state";
import { cn } from "@/lib/utils";

type UploadedFile = {
  name: string;
  path: string;
};

type IngestionLogEvent = {
  timestamp: string;
  event: string;
  message: string;
  source_path?: string;
  state?: string;
  processed_files?: number;
  total_files?: number;
  success_count?: number;
  failure_count?: number;
  skipped_count?: number;
  error?: string;
  provider?: string;
  model?: string;
  external_called?: boolean;
  fallback_reason?: string | null;
  vector_count?: number;
  embedding_provider?: string;
  embedding_model?: string;
  embedding_external_called?: boolean;
  embedding_fallback_reason?: string | null;
  embedding_fallback_method?: string | null;
  graph_extraction_provider?: string;
  graph_extraction_model?: string;
};

const terminalLogEvents = new Set(["batch_completed", "batch_failed", "batch_partial_failed"]);
const terminalBatchStates = new Set(["completed", "partial_failed", "failed", "skipped"]);

type FileBrowserItem = CourseFileSummary & {
  localOnly?: boolean;
};

const fileStatusMeta: Record<CourseFileStatus, { label: string; className: string }> = {
  pending: { label: "待解析", className: "border-amber-200/24 bg-amber-300/10 text-amber-100" },
  parsed: { label: "已解析", className: "border-emerald-200/24 bg-emerald-300/10 text-emerald-100" },
  parsing: { label: "解析中", className: "border-cyan-200/28 bg-cyan-300/10 text-cyan-100" },
  failed: { label: "解析失败", className: "border-rose-200/28 bg-rose-300/10 text-rose-100" },
  skipped: { label: "已跳过", className: "border-white/14 bg-white/[0.05] text-white/58" },
};

function FileStatusBadge({ status }: { status: CourseFileStatus }) {
  const meta = fileStatusMeta[status];
  const Icon = status === "parsed" ? CheckCircle2 : status === "failed" ? AlertCircle : status === "parsing" ? LoaderCircle : Clock3;
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs", meta.className, status === "parsing" && "animate-pulse")}>
      <Icon className={cn("size-3.5", status === "parsing" && "animate-spin")} />
      {meta.label}
    </span>
  );
}

const fileProgressMeta: Record<CourseFileStatus, { value: number; barClassName: string; pulse?: boolean }> = {
  pending: { value: 8, barClassName: "bg-amber-200/60" },
  parsing: { value: 58, barClassName: "bg-[linear-gradient(90deg,#64dfff,#7b7cff,#64dfff)]", pulse: true },
  parsed: { value: 100, barClassName: "bg-emerald-300/80" },
  failed: { value: 100, barClassName: "bg-rose-300/72" },
  skipped: { value: 100, barClassName: "bg-white/28" },
};

function FileProgressBar({ status }: { status: CourseFileStatus }) {
  const meta = fileProgressMeta[status];
  return (
    <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/7">
      <div
        className={cn("h-full rounded-full transition-[width] duration-500", meta.barClassName, meta.pulse && "animate-pulse")}
        style={{ width: `${meta.value}%` }}
      />
    </div>
  );
}

function fileNameFromPath(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}

function UploadWorkspaceContent({ selectedCourseId }: { selectedCourseId: string | null }) {
  const queryClient = useQueryClient();
  const [batchId, setBatchId] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState({ completed: 0, total: 0 });
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFile[]>([]);
  const [activeLogBatchId, setActiveLogBatchId] = useState<string | null>(null);
  const [logOpen, setLogOpen] = useState(false);
  const [logs, setLogs] = useState<IngestionLogEvent[]>([]);

  const dashboardQuery = useQuery({
    queryKey: ["dashboard", selectedCourseId],
    queryFn: () => fetchDashboard(selectedCourseId),
    enabled: Boolean(selectedCourseId),
  });
  const activeBatchId = batchId ?? dashboardQuery.data?.batch_status?.batch_id ?? null;
  const batchQuery = useQuery({
    queryKey: ["batch", selectedCourseId, activeBatchId],
    queryFn: () => fetchBatchStatus(activeBatchId as string),
    enabled: Boolean(activeBatchId),
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state && ["completed", "partial_failed", "failed"].includes(state) ? false : 3000;
    },
  });
  const courseFilesQuery = useQuery({
    queryKey: ["course-files", selectedCourseId],
    queryFn: () => fetchCourseFiles(selectedCourseId),
    enabled: Boolean(selectedCourseId),
    refetchInterval: () => (activeBatchId && !terminalBatchStates.has(batchQuery.data?.state ?? "") ? 3000 : false),
  });
  const visibleBatch = batchQuery.data && !terminalBatchStates.has(batchQuery.data.state) ? batchQuery.data : null;
  const parseTargetPaths = useMemo(() => {
    const remoteParseablePaths = (courseFilesQuery.data ?? []).filter((file) => file.status !== "parsing").map((file) => file.source_path);
    return Array.from(new Set([...uploadedFiles.map((file) => file.path), ...remoteParseablePaths]));
  }, [courseFilesQuery.data, uploadedFiles]);
  const uploadMutation = useMutation({
    mutationFn: async (files: File[]) => {
      setUploadProgress({ completed: 0, total: files.length });
      const responses = await Promise.all(
        files.map(async (file) => {
          const response = await uploadFile(file, selectedCourseId);
          setUploadProgress((progress) => ({ ...progress, completed: progress.completed + 1 }));
          return response;
        }),
      );
      return responses;
    },
    onSuccess: (data) => {
      setUploadedFiles((current) => [
        ...current,
        ...data.map((item) => ({
          name: fileNameFromPath(item.source_path),
          path: item.source_path,
        })),
      ]);
      void queryClient.invalidateQueries({ queryKey: ["course-files", selectedCourseId] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard", selectedCourseId] });
    },
    onSettled: () => {
      setUploadProgress({ completed: 0, total: 0 });
    },
  });

  const parseUploadsMutation = useMutation({
    mutationFn: () => parseUploadedFiles(parseTargetPaths, selectedCourseId),
    onSuccess: (data) => {
      setBatchId(data.batch_id);
      setActiveLogBatchId(data.batch_id);
      setLogs([]);
      setLogOpen(true);
      setUploadedFiles([]);
      void queryClient.invalidateQueries({ queryKey: ["course-files", selectedCourseId] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard", selectedCourseId] });
    },
  });

  const removeFileMutation = useMutation({
    mutationFn: (sourcePath: string) => removeCourseFile(sourcePath, selectedCourseId),
    onSuccess: (_data, sourcePath) => {
      setUploadedFiles((current) => current.filter((file) => file.path !== sourcePath));
      void queryClient.invalidateQueries({ queryKey: ["course-files", selectedCourseId] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard", selectedCourseId] });
      if (activeBatchId) {
        void queryClient.invalidateQueries({ queryKey: ["batch", selectedCourseId, activeBatchId] });
      }
    },
  });

  const uploadPercent = uploadProgress.total > 0 ? (uploadProgress.completed / uploadProgress.total) * 100 : 0;
  const modelAudit = useMemo(() => {
    const latestEmbeddingAudit = [...logs].reverse().find((item) => item.event === "embedding_audit");
    const initialAudit = logs.find((item) => item.event === "model_audit");
    const audit = latestEmbeddingAudit ?? initialAudit;
    if (!audit) {
      return "模型：等待后端返回模型审计";
    }
    const provider = audit.provider ?? audit.embedding_provider ?? "unknown";
    const embeddingModel = audit.model ?? audit.embedding_model ?? provider;
    const externalCalled = audit.external_called ?? audit.embedding_external_called ?? false;
    const fallbackReason = audit.fallback_reason ?? audit.embedding_fallback_reason ?? null;
    const fallbackMethod = audit.embedding_fallback_method ?? (provider === "fake" ? "deterministic_local_hash_embedding" : null);
    const graphProvider = audit.graph_extraction_provider;
    const graphModel = audit.graph_extraction_model ?? graphProvider;
    const embeddingText =
      provider === "fake"
        ? `embedding ${embeddingModel}（${provider} fallback: ${fallbackMethod ?? fallbackReason ?? "local fake"}）`
        : `embedding ${embeddingModel}（${provider}${externalCalled ? " 外部API" : ""}）`;
    const graphText = graphProvider ? `；图谱抽取 ${graphModel}（${graphProvider}）` : "";
    return `模型：${embeddingText}${graphText}`;
  }, [logs]);
  const fileItems = useMemo<FileBrowserItem[]>(() => {
    const remoteFiles = courseFilesQuery.data ?? [];
    const remotePaths = new Set(remoteFiles.map((file) => file.source_path));
    const pendingUploads = uploadedFiles
      .filter((file) => !remotePaths.has(file.path))
      .map<FileBrowserItem>((file) => ({
        id: `pending:${file.path}`,
        document_id: null,
        title: file.name,
        source_path: file.path,
        source_type: "unknown",
        chapter: null,
        status: "pending",
        job_state: null,
        batch_id: null,
        error: null,
        chunk_count: 0,
        updated_at: null,
        localOnly: true,
      }));
    return [...pendingUploads, ...remoteFiles];
  }, [courseFilesQuery.data, uploadedFiles]);

  const handleRemoveFile = (file: FileBrowserItem) => {
    if (file.localOnly) {
      setUploadedFiles((current) => current.filter((item) => item.path !== file.source_path));
      return;
    }
    removeFileMutation.mutate(file.source_path);
  };

  useEffect(() => {
    if (!activeLogBatchId) {
      return;
    }
    const source = new EventSource(getBatchLogUrl(activeLogBatchId));
    source.onmessage = (event) => {
      const item = JSON.parse(event.data) as IngestionLogEvent;
      setLogs((current) => [...current, item].slice(-300));
      if (terminalLogEvents.has(item.event)) {
        source.close();
        void queryClient.invalidateQueries({ queryKey: ["course-files", selectedCourseId] });
        void queryClient.invalidateQueries({ queryKey: ["dashboard", selectedCourseId] });
        void queryClient.invalidateQueries({ queryKey: ["batch", selectedCourseId, activeLogBatchId] });
      }
    };
    source.onerror = () => {
      source.close();
    };
    return () => {
      source.close();
    };
  }, [activeLogBatchId, queryClient, selectedCourseId]);

  useEffect(() => {
    if (batchQuery.data?.state && terminalBatchStates.has(batchQuery.data.state)) {
      setBatchId(null);
      void queryClient.invalidateQueries({ queryKey: ["course-files", selectedCourseId] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard", selectedCourseId] });
    }
  }, [batchQuery.data?.state, queryClient, selectedCourseId]);

  const inclusionRules = useMemo(
    () => [
      "纳入：PDF / ipynb / Markdown / TXT / DOCX / PPTX / 图片 OCR",
      "排除：output / tmp / scripts / .ipynb_checkpoints / LaTeX 中间文件 / xlsx / html 派生文件",
      "去重：同一路径同一 checksum 直接跳过，变更则新建 document version",
    ],
    [],
  );

  if (dashboardQuery.isLoading) {
    return <LoadingBlock rows={4} />;
  }
  if (dashboardQuery.error) {
    return <ErrorBlock message={(dashboardQuery.error as Error).message} />;
  }

  return (
    <div className="kg-page">
      <section className="glass-panel relative grid min-h-[calc(100dvh-8rem)] overflow-hidden rounded-[34px] xl:h-[calc(100dvh-8rem)] xl:min-h-0 xl:grid-cols-[minmax(280px,0.82fr)_minmax(420px,1.32fr)_minmax(320px,0.92fr)]">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_18%_0%,rgba(86,217,255,0.12),transparent_32%),radial-gradient(circle_at_88%_20%,rgba(34,197,94,0.08),transparent_28%),linear-gradient(180deg,rgba(255,255,255,0.035),transparent_34%)]" />
        <div className="relative border-b border-white/8 p-6 xl:border-b-0 xl:border-r xl:p-7">
        <div className="grid gap-6">
          <div className="space-y-5">
            <p className="section-kicker">Ingestion Console</p>
            <h2 className="glow-text text-4xl font-semibold text-white lg:text-5xl">全量导入控制台</h2>
            <p className="max-w-2xl text-base leading-8 text-cyan-50/72">
              文件上传后会进入本课程 storage 文件夹。文件导览中的任意入库文件都可以直接解析、切块、向量化并更新图谱。
            </p>

            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => parseUploadsMutation.mutate()}
                disabled={parseUploadsMutation.isPending || parseTargetPaths.length === 0}
                className="rounded-full border border-emerald-300/35 bg-emerald-300/10 px-5 py-3 text-sm uppercase tracking-[0.24em] text-white disabled:opacity-50"
              >
                {parseUploadsMutation.isPending ? <LoaderCircle className="mr-2 inline size-4 animate-spin" /> : <FileCheck2 className="mr-2 inline size-4" />}
                {parseUploadsMutation.isPending ? "解析中" : "解析文件"}
              </button>
              <label
                aria-disabled={uploadMutation.isPending}
                className={`cursor-pointer rounded-full border border-white/12 px-5 py-3 text-sm uppercase tracking-[0.24em] text-white/72 transition hover:text-white ${
                  uploadMutation.isPending ? "pointer-events-none opacity-65" : ""
                }`}
              >
                {uploadMutation.isPending ? <LoaderCircle className="mr-2 inline size-4 animate-spin" /> : <UploadCloud className="mr-2 inline size-4" />}
                {uploadMutation.isPending ? `上传中 ${uploadProgress.completed}/${uploadProgress.total}` : "上传文件"}
                <input
                  type="file"
                  multiple
                  disabled={uploadMutation.isPending}
                  className="hidden"
                  onChange={(event) => {
                    const files = Array.from(event.target.files ?? []);
                    event.target.value = "";
                    if (files.length > 0) {
                      uploadMutation.mutate(files);
                    }
                  }}
                />
              </label>
              <button
                type="button"
                onClick={() => {
                  if (activeBatchId) {
                    setActiveLogBatchId(activeBatchId);
                    setLogOpen(true);
                  }
                }}
                disabled={!activeBatchId}
                className="rounded-full border border-white/12 px-4 py-3 text-white/72 transition hover:text-white disabled:opacity-40"
              >
                <PanelRightOpen className="size-4" />
              </button>
            </div>
            {uploadMutation.isPending ? (
              <div className="max-w-md">
                <div className="flex items-center justify-between text-xs uppercase tracking-[0.22em] text-white/45">
                  <span>上传进度</span>
                  <span>{Math.round(uploadPercent)}%</span>
                </div>
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/8">
                  <div className="h-full rounded-full bg-[linear-gradient(90deg,#64dfff,#7b7cff)] transition-[width] duration-300" style={{ width: `${uploadPercent}%` }} />
                </div>
              </div>
            ) : null}
            {uploadedFiles.length > 0 ? (
              <div className="max-w-2xl border-l border-cyan-200/20 bg-cyan-300/[0.035] py-3 pl-4 pr-2">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-xs uppercase tracking-[0.24em] text-white/45">已上传，待解析</p>
                  <button type="button" className="text-xs uppercase tracking-[0.2em] text-white/48 hover:text-white" onClick={() => setUploadedFiles([])}>
                    清空
                  </button>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {uploadedFiles.map((file) => (
                    <span key={file.path} className="max-w-full truncate rounded-full border border-white/10 px-3 py-1 text-sm text-cyan-50/72">
                      {file.name}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
          </div>

          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-1">
            {[
              {
                label: "最新批次",
                value: visibleBatch?.state ?? "idle",
                hint: visibleBatch ? `${visibleBatch.processed_files}/${visibleBatch.total_files}` : "等待启动",
              },
              {
                label: "最近上传",
                value: uploadedFiles.length > 0 ? String(uploadedFiles.length) : "idle",
                hint: uploadedFiles.length > 0 ? "等待解析" : "暂无待解析上传",
              },
              {
                label: "已入库文档",
                value: String(dashboardQuery.data?.ingested_document_count ?? 0),
                hint: "当前课程有效版本",
              },
              {
                label: "图谱关系",
                value: String(dashboardQuery.data?.graph_relation_count ?? 0),
                hint: "概念关系边总数",
              },
            ].map((item) => (
              <div key={item.label} className="border-l border-white/10 px-4 py-3">
                <p className="text-xs uppercase tracking-[0.3em] text-white/45">{item.label}</p>
                <p className="mt-3 text-3xl font-semibold text-white">{item.value}</p>
                <p className="mt-2 text-sm text-white/55">{item.hint}</p>
              </div>
            ))}
          </div>
        </div>
        </div>

        <div className="relative flex min-h-[540px] max-h-[72dvh] flex-col border-b border-white/8 p-6 xl:h-full xl:min-h-0 xl:max-h-none xl:border-b-0 xl:border-r xl:p-7">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="section-kicker">File Library</p>
            <h3 className="mt-2 text-2xl font-semibold text-white">已入库文件导览</h3>
            <p className="mt-2 text-sm text-white/50">本课程 storage 文件夹中的文件会统一显示在这里。</p>
          </div>
          <span className="kg-micro-chip rounded-full px-3 py-2 text-xs">{fileItems.length} 个文件</span>
        </div>

        <div className="custom-scrollbar kg-rounded-scrollbar mt-5 min-h-[18rem] flex-1 overflow-y-auto overscroll-contain rounded-[24px] border border-white/8 bg-black/10 pr-1">
          {courseFilesQuery.isLoading && fileItems.length === 0 ? (
            <div className="kg-shimmer px-5 py-8 text-sm text-white/50">正在加载文件...</div>
          ) : fileItems.length === 0 ? (
            <div className="px-5 py-8 text-sm text-white/50">暂无文件。上传文件后会先显示为待解析。</div>
          ) : (
            fileItems.map((file) => (
              <div key={`${file.id}-${file.source_path}`} className="border-b border-white/7 px-4 py-4 last:border-b-0 transition hover:bg-white/[0.035]">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="max-w-full truncate text-sm font-medium text-white">{file.title || fileNameFromPath(file.source_path)}</p>
                      <FileStatusBadge status={file.status} />
                    </div>
                    <p className="mt-2 break-all text-xs leading-5 text-white/42">{file.source_path}</p>
                    {file.error ? <p className="mt-2 break-words text-xs leading-5 text-rose-100/70">{file.error}</p> : null}
                    <FileProgressBar status={file.status} />
                  </div>
                  <button
                    type="button"
                    onClick={() => handleRemoveFile(file)}
                    disabled={file.status === "parsing" || removeFileMutation.isPending}
                    className="inline-flex shrink-0 items-center gap-2 rounded-full border border-white/10 px-3 py-2 text-xs text-white/62 transition hover:border-rose-200/35 hover:text-rose-100 disabled:pointer-events-none disabled:opacity-40"
                    title={file.status === "parsing" ? "解析中，暂不能移除" : "移除文件"}
                  >
                    <Trash2 className="size-3.5" />
                    移除
                  </button>
                </div>
                <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-white/42">
                  <span className="rounded-full border border-white/8 px-2.5 py-1">{file.source_type || "unknown"}</span>
                  {file.chapter ? <span className="rounded-full border border-white/8 px-2.5 py-1">{file.chapter}</span> : null}
                  <span className="rounded-full border border-white/8 px-2.5 py-1">{file.chunk_count} chunks</span>
                </div>
              </div>
            ))
          )}
        </div>
        </div>

        <div className="relative min-h-0">
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="border-b border-white/8 p-6 xl:p-7">
          <div className="flex items-center justify-between">
            <div>
              <p className="section-kicker">Rules</p>
              <h3 className="mt-2 text-2xl font-semibold text-white">纳入与排除策略</h3>
            </div>
            <Files className="size-5 text-cyan-200" />
          </div>
          <div className="mt-6 space-y-3">
            {inclusionRules.map((rule) => (
              <div key={rule} className="border-l border-white/10 px-4 py-3 text-sm leading-7 text-white/70">
                {rule}
              </div>
            ))}
          </div>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.08 }} className="p-6 xl:p-7">
          <div className="flex items-center justify-between">
            <div>
              <p className="section-kicker">Batch Status</p>
              <h3 className="mt-2 text-2xl font-semibold text-white">当前后台批次</h3>
            </div>
            <button
              type="button"
              className="rounded-full border border-white/10 p-2 text-white/65 transition hover:text-white"
              onClick={() => {
                void queryClient.invalidateQueries({ queryKey: ["dashboard", selectedCourseId] });
                if (activeBatchId) {
                  void queryClient.invalidateQueries({ queryKey: ["batch", selectedCourseId, activeBatchId] });
                }
              }}
            >
              <RefreshCcw className="size-4" />
            </button>
          </div>

          <div className="mt-6 space-y-5">
            <div className="border border-white/8 bg-black/10 p-5">
              <div className="flex items-center justify-between gap-4">
                <p className="text-lg font-medium text-white">{visibleBatch?.state ?? "未启动"}</p>
                <p className="text-xs uppercase tracking-[0.26em] text-white/45">{visibleBatch ? visibleBatch.batch_id.slice(0, 8) : "no-batch"}</p>
              </div>
              <div className="mt-4 h-2 overflow-hidden rounded-full bg-white/6">
                <div
                  className="h-full rounded-full bg-[linear-gradient(90deg,#64dfff,#7b7cff)]"
                  style={{
                    width: `${visibleBatch?.total_files ? (visibleBatch.processed_files / visibleBatch.total_files) * 100 : 0}%`,
                  }}
                />
              </div>
              <div className="mt-4 grid gap-3 sm:grid-cols-3">
                {[
                  { label: "成功", value: visibleBatch?.success_count ?? 0 },
                  { label: "跳过", value: visibleBatch?.skipped_count ?? 0 },
                  { label: "失败", value: visibleBatch?.failure_count ?? 0 },
                ].map((item) => (
                  <div key={item.label} className="border-l border-white/10 px-4 py-2">
                    <p className="text-xs uppercase tracking-[0.24em] text-white/45">{item.label}</p>
                    <p className="mt-2 text-2xl font-semibold text-white">{item.value}</p>
                  </div>
                ))}
              </div>
            </div>

            {(visibleBatch?.errors ?? []).length > 0 ? (
              <div className="rounded-[22px] border border-rose-300/20 bg-rose-400/[0.05] p-5">
                <p className="text-xs uppercase tracking-[0.26em] text-rose-100/70">Failures</p>
                <div className="mt-4 space-y-3">
                  {visibleBatch?.errors.slice(0, 6).map((error) => (
                    <div key={`${error.source_path}-${error.message}`} className="rounded-[18px] border border-white/8 px-4 py-3 text-sm text-white/72">
                      <p className="font-medium text-white">{error.source_path}</p>
                      <p className="mt-1 text-white/58">{error.message}</p>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        </motion.div>
        </div>
      </section>

      {logOpen ? (
        <div className="fixed inset-y-0 right-0 z-50 flex w-full justify-end bg-black/24 backdrop-blur-[2px] sm:w-auto sm:bg-transparent">
          <motion.aside
            initial={{ x: 420, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: 420, opacity: 0 }}
            className="h-full w-full max-w-[420px] border-l border-white/10 bg-[rgba(3,7,20,0.94)] p-5 text-white shadow-[0_0_60px_rgba(0,0,0,0.45)]"
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="section-kicker">解析日志</p>
                <h3 className="mt-2 text-xl font-semibold">导入日志流</h3>
                <p className="mt-1 text-xs text-white/45">{(activeLogBatchId ?? activeBatchId) ? (activeLogBatchId ?? activeBatchId)?.slice(0, 8) : "no batch"}</p>
              </div>
              <button type="button" onClick={() => setLogOpen(false)} className="rounded-full border border-white/10 p-2 text-white/62 transition hover:text-white">
                <X className="size-4" />
              </button>
            </div>
            <p className="mt-3 rounded-full border border-cyan-200/12 bg-cyan-300/[0.045] px-3 py-2 text-[11px] leading-5 text-cyan-50/62">
              {modelAudit}
            </p>

            <div className="mt-4 h-[calc(100%-132px)] overflow-y-auto pr-1">
              {logs.length > 0 ? (
                <div className="space-y-3">
                  {logs.map((item, index) => (
                    <div key={`${item.timestamp}-${index}`} className="rounded-[18px] border border-white/8 bg-white/[0.03] px-4 py-3">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-xs uppercase tracking-[0.2em] text-cyan-100/54">{item.event.replaceAll("_", " ")}</span>
                        <span className="text-[11px] text-white/36">{new Date(item.timestamp).toLocaleTimeString()}</span>
                      </div>
                      <p className="mt-2 break-words text-sm leading-6 text-white/72">{item.message}</p>
                      {typeof item.processed_files === "number" && typeof item.total_files === "number" ? (
                        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/8">
                          <div
                            className="h-full rounded-full bg-[linear-gradient(90deg,#64dfff,#7b7cff)] transition-[width] duration-300"
                            style={{ width: `${item.total_files ? (item.processed_files / item.total_files) * 100 : 0}%` }}
                          />
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-[18px] border border-white/8 bg-white/[0.03] px-4 py-5 text-sm text-white/54">等待解析日志...</div>
              )}
            </div>
          </motion.aside>
        </div>
      ) : null}
    </div>
  );
}

export function UploadWorkspace() {
  const { selectedCourseId } = useCourseContext();
  return <UploadWorkspaceContent key={selectedCourseId ?? "unassigned"} selectedCourseId={selectedCourseId} />;
}
