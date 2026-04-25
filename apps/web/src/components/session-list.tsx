"use client";

import type { SessionSummary } from "@course-kg/shared";
import { MessageSquare } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";

interface SessionListProps {
  sessions: SessionSummary[];
  activeSessionId?: string | null;
  onSelect: (sessionId: string) => void;
  onNew: () => void;
}

export function SessionList({ sessions, activeSessionId, onSelect, onNew }: SessionListProps) {
  return (
    <Card className="min-h-0 border-white/10 bg-white/[0.03] text-white">
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-3">
          <span>会话</span>
          <Button type="button" variant="outline" size="sm" onClick={onNew}>
            新建
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent className="min-h-0">
        <ScrollArea className="h-[360px] pr-3">
          <div className="flex flex-col gap-2">
            {sessions.length === 0 ? (
              <div className="rounded-lg border border-white/10 px-4 py-5 text-sm text-white/55">暂无保存的会话。</div>
            ) : (
              sessions.map((session) => (
                <button
                  key={session.id}
                  type="button"
                  onClick={() => onSelect(session.id)}
                  className={`rounded-lg border px-3 py-3 text-left transition ${
                    session.id === activeSessionId
                      ? "border-cyan-300/40 bg-cyan-300/10"
                      : "border-white/10 bg-black/10 hover:border-cyan-300/30"
                  }`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="min-w-0 truncate text-sm font-medium">{session.title ?? "未命名会话"}</span>
                    <Badge variant="outline">
                      <MessageSquare data-icon="inline-start" />
                      {session.transcript.length}
                    </Badge>
                  </div>
                  {session.last_question ? <p className="mt-2 line-clamp-2 text-xs leading-5 text-white/48">{session.last_question}</p> : null}
                </button>
              ))
            )}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  );
}
