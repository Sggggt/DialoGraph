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

export function AgentTracePanel({ trace }: AgentTracePanelProps) {
  return (
    <Card className="min-h-0 border-white/10 bg-white/[0.03] text-white">
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-3">
          <span>Agent Trace</span>
          <Badge variant="outline">{trace.length} steps</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="min-h-0">
        <ScrollArea className="h-[360px] pr-3">
          <div className="flex flex-col gap-3">
            {trace.length === 0 ? (
              <div className="rounded-lg border border-white/10 px-4 py-5 text-sm text-white/55">
                Trace events appear as the graph runs.
              </div>
            ) : (
              trace.map((event, index) => {
                const Icon = event.status === "failed" ? XCircle : event.duration_ms > 0 ? CheckCircle2 : Activity;
                return (
                  <div key={event.id ?? `${event.node}-${index}`} className="rounded-lg border border-white/10 bg-black/10 p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-2">
                        <Icon data-icon="inline-start" />
                        <span className="truncate text-sm font-medium">{event.node}</span>
                      </div>
                      <Badge variant={event.status === "failed" ? "destructive" : "secondary"}>
                        <Clock3 data-icon="inline-start" />
                        {event.duration_ms} ms
                      </Badge>
                    </div>
                    {event.output_summary ? <MarkdownRenderer content={event.output_summary} compact className="mt-3 text-white/62" /> : null}
                    {event.document_ids.length > 0 ? (
                      <p className="mt-2 text-xs text-white/38">{event.document_ids.length} chunks touched</p>
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
