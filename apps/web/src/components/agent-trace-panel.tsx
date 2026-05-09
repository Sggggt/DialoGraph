"use client";

import type { AgentTraceEventPayload } from "@course-kg/shared";
import { Activity, CheckCircle2, Clock3, XCircle } from "lucide-react";

import { MarkdownRenderer } from "@/components/markdown-renderer";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";

interface AgentTracePanelProps {
  trace: AgentTraceEventPayload[];
}

const traceNodeLabels: Record<string, string> = {
  perception: "感知",
  retrieval_planner: "检索规划",
  query_analyzer: "问题分析",
  router: "路由判断",
  query_rewriter: "查询改写",
  retrieval_decision: "检索决策",
  retrievers: "检索召回",
  document_grader: "证据筛选",
  evidence_evaluator: "证据评估",
  retry_planner: "重试规划",
  context_synthesizer: "上下文合成",
  answer_generator: "答案生成",
  citation_checker: "引用校验",
  citation_verifier: "引用验证",
  reflection: "反思",
  answer_corrector: "答案修正",
  self_check: "自检",
  error: "错误",
};

function traceNodeLabel(node: string): string {
  return traceNodeLabels[node] ?? node;
}

function traceBadgeVariant(node: string, output: string | null, status: string): "default" | "secondary" | "destructive" | "outline" {
  if (status === "failed") return "destructive";
  if (node === "reflection" && output?.includes("has_issue=True")) return "destructive";
  if (node === "retrieval_decision" && output?.includes("skip_retrieval=True")) return "outline";
  if (node === "citation_verifier" && output && parseInt(output.match(/(\d+) unverified/)?.[1] ?? "0", 10) > 0) return "destructive";
  return "secondary";
}

export function AgentTracePanel({ trace }: AgentTracePanelProps) {
  return (
    <Card className="min-h-0 border-white/10 bg-white/[0.03] text-white">
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-3">
          <span>智能体轨迹</span>
          <Badge variant="outline">{trace.length} 步</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="min-h-0">
        <ScrollArea className="h-[360px] pr-3">
          <div className="flex flex-col gap-3">
            {trace.length === 0 ? (
              <div className="rounded-lg border border-white/10 px-4 py-5 text-sm text-white/55">
                智能体开始运行后会在这里显示每个节点的轨迹。
              </div>
            ) : (
              trace.map((event, index) => {
                const Icon = event.status === "failed" ? XCircle : event.duration_ms > 0 ? CheckCircle2 : Activity;
                const badgeVariant = traceBadgeVariant(event.node, event.output_summary ?? null, event.status);
                return (
                  <div key={event.id ?? `${event.node}-${index}`} className="rounded-lg border border-white/10 bg-black/10 p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-2">
                        <Icon data-icon="inline-start" />
                        <span className="truncate text-sm font-medium">{traceNodeLabel(event.node)}</span>
                        {event.node === "reflection" && event.output_summary?.includes("has_issue=True") ? (
                          <Badge variant="destructive" className="text-[10px]">需修正</Badge>
                        ) : null}
                        {event.node === "retrieval_decision" && event.output_summary?.includes("skip_retrieval=True") ? (
                          <Badge variant="outline" className="text-[10px]">跳过检索</Badge>
                        ) : null}
                      </div>
                      <Badge variant={badgeVariant}>
                        <Clock3 data-icon="inline-start" />
                        {event.duration_ms} ms
                      </Badge>
                    </div>
                    {event.output_summary ? <MarkdownRenderer content={event.output_summary} compact className="mt-3 text-white/62" /> : null}
                    {event.document_ids.length > 0 ? (
                      <p className="mt-2 text-xs text-white/38">触达 {event.document_ids.length} 个片段</p>
                    ) : null}
                  </div>
                );
              })
            )}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  );
}
