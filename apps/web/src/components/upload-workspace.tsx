"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { Files, RefreshCcw, Rocket, UploadCloud } from "lucide-react";

import { fetchBatchStatus, fetchDashboard, fetchJobStatus, startSourceSync, uploadFile } from "@/lib/api";
import { useCourseContext } from "@/components/course-context";
import { ErrorBlock, LoadingBlock } from "@/components/query-state";

function UploadWorkspaceContent({ selectedCourseId }: { selectedCourseId: string | null }) {
  const queryClient = useQueryClient();
  const [batchId, setBatchId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

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
  const jobQuery = useQuery({
    queryKey: ["job", selectedCourseId, jobId],
    queryFn: () => fetchJobStatus(jobId as string),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state && ["completed", "failed", "skipped"].includes(state) ? false : 2000;
    },
  });

  const syncMutation = useMutation({
    mutationFn: () => startSourceSync(selectedCourseId),
    onSuccess: (data) => {
      setBatchId(data.batch_id);
      void queryClient.invalidateQueries({ queryKey: ["dashboard", selectedCourseId] });
    },
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadFile(file, selectedCourseId),
    onSuccess: (data) => {
      setJobId(data.job_id);
      void queryClient.invalidateQueries({ queryKey: ["dashboard", selectedCourseId] });
    },
  });

  useEffect(() => {
    if (batchQuery.data?.state && ["completed", "partial_failed", "failed"].includes(batchQuery.data.state)) {
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
    <div className="kg-page grid gap-6 xl:grid-rows-[auto_minmax(0,1fr)]">
      <section className="glass-panel rounded-[30px] p-6 lg:p-8">
        <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
          <div className="space-y-5">
            <p className="section-kicker">Ingestion Console</p>
            <h2 className="glow-text text-4xl font-semibold text-white lg:text-5xl">全量导入控制台</h2>
            <p className="max-w-2xl text-base leading-8 text-cyan-50/72">
              针对当前配置的课程源目录执行原始资料扫描。批次会在后台完成解析、切块、向量化和图谱更新。
            </p>

            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => syncMutation.mutate()}
                disabled={syncMutation.isPending}
                className="rounded-full border border-cyan-300/40 bg-cyan-300/12 px-5 py-3 text-sm uppercase tracking-[0.24em] text-white disabled:opacity-50"
              >
                <Rocket className="mr-2 inline size-4" />
                {syncMutation.isPending ? "Starting" : "Start Full Sync"}
              </button>
              <label className="cursor-pointer rounded-full border border-white/12 px-5 py-3 text-sm uppercase tracking-[0.24em] text-white/72 transition hover:text-white">
                <UploadCloud className="mr-2 inline size-4" />
                Upload Single File
                <input
                  type="file"
                  className="hidden"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) {
                      uploadMutation.mutate(file);
                    }
                  }}
                />
              </label>
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            {[
              {
                label: "最新批次",
                value: batchQuery.data?.state ?? dashboardQuery.data?.batch_status?.state ?? "idle",
                hint: batchQuery.data ? `${batchQuery.data.processed_files}/${batchQuery.data.total_files}` : "等待启动",
              },
              {
                label: "最近上传",
                value: jobQuery.data?.state ?? "idle",
                hint: jobQuery.data?.source_path ?? "未单独上传",
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
              <div key={item.label} className="rounded-[24px] border border-white/8 bg-white/[0.03] p-5">
                <p className="text-xs uppercase tracking-[0.3em] text-white/45">{item.label}</p>
                <p className="mt-3 text-3xl font-semibold text-white">{item.value}</p>
                <p className="mt-2 text-sm text-white/55">{item.hint}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="grid min-h-0 gap-6 lg:grid-cols-[0.8fr_1.2fr]">
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="glass-panel kg-scroll-panel rounded-[28px] p-6">
          <div className="flex items-center justify-between">
            <div>
              <p className="section-kicker">Rules</p>
              <h3 className="mt-2 text-2xl font-semibold text-white">纳入与排除策略</h3>
            </div>
            <Files className="size-5 text-cyan-200" />
          </div>
          <div className="mt-6 space-y-3">
            {inclusionRules.map((rule) => (
              <div key={rule} className="rounded-[20px] border border-white/8 bg-white/[0.03] px-4 py-4 text-sm leading-7 text-white/70">
                {rule}
              </div>
            ))}
          </div>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.08 }} className="glass-panel kg-scroll-panel rounded-[28px] p-6">
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
            <div className="rounded-[22px] border border-white/8 bg-white/[0.03] p-5">
              <div className="flex items-center justify-between gap-4">
                <p className="text-lg font-medium text-white">{batchQuery.data?.state ?? "未启动"}</p>
                <p className="text-xs uppercase tracking-[0.26em] text-white/45">{activeBatchId ? activeBatchId.slice(0, 8) : "no-batch"}</p>
              </div>
              <div className="mt-4 h-2 overflow-hidden rounded-full bg-white/6">
                <div
                  className="h-full rounded-full bg-[linear-gradient(90deg,#64dfff,#7b7cff)]"
                  style={{
                    width: `${batchQuery.data?.total_files ? (batchQuery.data.processed_files / batchQuery.data.total_files) * 100 : 0}%`,
                  }}
                />
              </div>
              <div className="mt-4 grid gap-3 sm:grid-cols-3">
                {[
                  { label: "成功", value: batchQuery.data?.success_count ?? 0 },
                  { label: "跳过", value: batchQuery.data?.skipped_count ?? 0 },
                  { label: "失败", value: batchQuery.data?.failure_count ?? 0 },
                ].map((item) => (
                  <div key={item.label} className="rounded-[18px] border border-white/8 px-4 py-3">
                    <p className="text-xs uppercase tracking-[0.24em] text-white/45">{item.label}</p>
                    <p className="mt-2 text-2xl font-semibold text-white">{item.value}</p>
                  </div>
                ))}
              </div>
            </div>

            {(batchQuery.data?.errors ?? []).length > 0 ? (
              <div className="rounded-[22px] border border-rose-300/20 bg-rose-400/[0.05] p-5">
                <p className="text-xs uppercase tracking-[0.26em] text-rose-100/70">Failures</p>
                <div className="mt-4 space-y-3">
                  {batchQuery.data?.errors.slice(0, 6).map((error) => (
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
      </section>
    </div>
  );
}

export function UploadWorkspace() {
  const { selectedCourseId } = useCourseContext();
  return <UploadWorkspaceContent key={selectedCourseId ?? "unassigned"} selectedCourseId={selectedCourseId} />;
}
