"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { AgentResponse, AgentTraceEventPayload, Citation, SessionSummary } from "@course-kg/shared";
import { motion } from "framer-motion";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  BrainCircuit,
  ChevronRight,
  CircleDot,
  FileText,
  History,
  Layers3,
  Loader2,
  Plus,
  Send,
  ShieldCheck,
  Sparkles,
  Waypoints,
} from "lucide-react";

import { CitationCard } from "@/components/citation-card";
import { useCourseContext } from "@/components/course-context";
import { MarkdownRenderer } from "@/components/markdown-renderer";
import { ErrorBlock, LoadingBlock } from "@/components/query-state";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { fetchDashboard, fetchSessionMessages, fetchSessions, streamAnswer } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useLocalStorage } from "@/hooks/use-local-storage";

type ChatTurn = {
  role: "user" | "assistant";
  content: string;
  run_id?: string | null;
  route?: string | null;
  citations?: Citation[];
};

const suggestions = [
  "Compare adjacency matrix and Laplacian matrix",
  "Explain eigenvector centrality with a course citation",
  "Find exercise-style material about PageRank",
  "How does algebraic connectivity relate to network robustness?",
];

function normalizeMessages(messages: Array<Record<string, unknown>>): ChatTurn[] {
  return messages
    .filter((item) => item.role === "user" || item.role === "assistant")
    .map((item) => ({
      role: item.role as "user" | "assistant",
      content: String(item.content ?? ""),
      run_id: typeof item.run_id === "string" ? item.run_id : null,
      citations: Array.isArray(item.citations) ? (item.citations as Citation[]) : undefined,
    }));
}

function ChatHeader({
  grounded,
  chapterList,
  latestRun,
  onOpenSessions,
  onOpenTrace,
  onOpenCitations,
  citationsCount,
}: {
  grounded: boolean;
  chapterList: string;
  latestRun: AgentResponse | null;
  onOpenSessions: () => void;
  onOpenTrace: () => void;
  onOpenCitations: () => void;
  citationsCount: number;
}) {
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-3 px-1">
      <div className="min-w-0">
        <p className="section-kicker">Agentic Course Chat</p>
        <h2 className="mt-1 text-2xl font-semibold text-white lg:text-3xl">Ask the reasoning graph</h2>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="outline" className="rounded-full" onClick={onOpenSessions}>
          <History data-icon="inline-start" />
          Sessions
        </Button>
        <Button type="button" variant="outline" className="rounded-full" onClick={onOpenTrace}>
          <Waypoints data-icon="inline-start" />
          Trace
        </Button>
        <Button type="button" variant="outline" className="rounded-full" onClick={onOpenCitations}>
          <FileText data-icon="inline-start" />
          Citations {citationsCount}
        </Button>
      </div>
      <div className="flex w-full flex-wrap gap-2">
        <span className="kg-micro-chip rounded-full px-3 py-2 text-xs">
          <ShieldCheck data-icon="inline-start" />
          {grounded ? "Grounded" : "Fallback"}
        </span>
        <span className="kg-micro-chip max-w-full truncate rounded-full px-3 py-2 text-xs">Chapters: {chapterList || "waiting"}</span>
        {latestRun?.route ? (
          <span className="kg-micro-chip rounded-full px-3 py-2 text-xs">
            <BrainCircuit data-icon="inline-start" />
            {latestRun.route}
          </span>
        ) : null}
      </div>
    </div>
  );
}

function SuggestionChips({ onPick }: { onPick: (value: string) => void }) {
  return (
    <div className="mx-auto flex max-w-3xl flex-wrap justify-center gap-2">
      {suggestions.map((suggestion) => (
        <motion.button
          key={suggestion}
          type="button"
          whileHover={{ y: -2 }}
          whileTap={{ scale: 0.98 }}
          onClick={() => onPick(suggestion)}
          className="kg-micro-chip rounded-full px-4 py-2 text-sm transition hover:border-cyan-200/30 hover:text-white"
        >
          {suggestion}
        </motion.button>
      ))}
    </div>
  );
}

function EmptyChatState({ onPick }: { onPick: (value: string) => void }) {
  return (
    <div className="grid min-h-[calc(100dvh-21rem)] place-items-center px-4 pb-44 pt-12 text-center">
      <div className="-translate-y-24">
        <div className="mx-auto grid size-16 place-items-center rounded-3xl border border-cyan-200/14 bg-cyan-300/[0.045] text-cyan-100 shadow-[0_0_42px_rgba(86,217,255,0.08)]">
          <Sparkles />
        </div>
        <h3 className="glow-text mt-6 text-3xl font-semibold text-white">Start a grounded course conversation</h3>
        <p className="mx-auto mt-3 max-w-2xl text-sm leading-7 text-white/56">
          The agent will route your question, retrieve course fragments, grade evidence, generate an answer, and verify citations.
        </p>
        <div className="mt-7">
          <SuggestionChips onPick={onPick} />
        </div>
      </div>
    </div>
  );
}

function MessageBubble({ turn, index, onOpenCitations }: { turn: ChatTurn; index: number; onOpenCitations: () => void }) {
  const isUser = turn.role === "user";
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(index * 0.025, 0.18) }}
      className={cn("flex", isUser ? "justify-end" : "justify-start")}
    >
      <div
        className={cn(
          "relative max-w-[min(860px,92%)] px-1 py-3",
          isUser
            ? "rounded-[1.25rem] border border-cyan-200/12 bg-cyan-300/[0.045] px-5 shadow-[0_0_24px_rgba(86,217,255,0.035)]"
            : "w-full border-l border-cyan-200/18 pl-5 text-white",
        )}
      >
        <div className="mb-3 flex items-center gap-2 text-xs uppercase tracking-[0.2em] text-white/38">
          {isUser ? <CircleDot /> : <BrainCircuit />}
          {isUser ? "You" : turn.route ? `Agent / ${turn.route}` : "Agent"}
        </div>
        <MarkdownRenderer content={turn.content} className={cn(isUser ? "text-white/78" : "text-white/74")} />
        {!isUser && turn.citations?.length ? (
          <button type="button" onClick={onOpenCitations} className="kg-micro-chip mt-4 rounded-full px-3 py-2 text-xs transition hover:border-cyan-200/30 hover:text-white">
            <FileText />
            {turn.citations.length} verified sources
          </button>
        ) : null}
      </div>
    </motion.div>
  );
}

function GeneratingBubble({ content }: { content: string }) {
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="flex justify-start">
      <div className="w-full max-w-[min(860px,92%)] border-l border-cyan-200/18 px-5 py-4 text-white">
        <div className="mb-3 flex items-center gap-2 text-xs uppercase tracking-[0.2em] text-cyan-100/50">
          <span className="tech-dot" />
          Generating
        </div>
        {content ? (
          <div className="relative">
            <MarkdownRenderer content={content} className="pr-3 text-white/76" />
            <span className="stream-cursor">|</span>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-sm text-white/56">
            <span className="signal-bars">
              <span />
              <span />
              <span />
              <span />
            </span>
            Routing, retrieving, and checking evidence...
          </div>
        )}
      </div>
    </motion.div>
  );
}

function MessageList({
  turns,
  isGenerating,
  draftAnswer,
  onPickSuggestion,
  onOpenCitations,
}: {
  turns: ChatTurn[];
  isGenerating: boolean;
  draftAnswer: string;
  onPickSuggestion: (value: string) => void;
  onOpenCitations: () => void;
}) {
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, draftAnswer]);

  return (
    <div className="relative min-h-[calc(100dvh-21rem)]">
      {turns.length === 0 && !isGenerating ? (
        <EmptyChatState onPick={onPickSuggestion} />
      ) : (
        <div className="mx-auto flex max-w-5xl flex-col gap-8 px-1 pb-52 pt-4">
          {turns.map((turn, index) => (
            <MessageBubble key={`${turn.role}-${index}-${turn.run_id ?? "local"}`} turn={turn} index={index} onOpenCitations={onOpenCitations} />
          ))}
          {isGenerating ? <GeneratingBubble content={draftAnswer} /> : null}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  );
}

function ChatComposer({
  value,
  onChange,
  onSubmit,
  isPending,
  activeSessionId,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  isPending: boolean;
  activeSessionId: string | null;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      className="pointer-events-none fixed inset-x-4 bottom-4 z-30 lg:left-[calc(76px+1.75rem)] lg:right-7"
    >
      <div className="pointer-events-auto mx-auto w-full max-w-5xl">
        <div
          className={cn(
            "kg-scan-edge rounded-[1.7rem] border border-cyan-200/16 bg-[rgba(7,13,31,0.68)] p-2 shadow-[0_20px_70px_rgba(0,0,0,0.34),0_0_42px_rgba(86,217,255,0.08)] backdrop-blur-2xl",
            isPending && "border-cyan-100/24 shadow-[0_20px_70px_rgba(0,0,0,0.34),0_0_58px_rgba(86,217,255,0.14)]",
          )}
        >
          <div className="flex flex-col gap-3 rounded-[1.35rem] bg-[linear-gradient(135deg,rgba(86,217,255,0.065),rgba(122,95,255,0.035)_55%,rgba(0,0,0,0.12))] p-3">
            <div className="flex flex-wrap items-center gap-2 px-1">
              <span className="kg-micro-chip rounded-full px-2.5 py-1 text-[11px]">
                <Layers3 />
                Course context
              </span>
              <span className="kg-micro-chip max-w-full truncate rounded-full px-2.5 py-1 text-[11px]">
                Session {activeSessionId ? activeSessionId.slice(0, 8) : "new"}
              </span>
            </div>
            <div className="flex items-end gap-3">
              <Textarea
                value={value}
                onChange={(event) => onChange(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    onSubmit();
                  }
                }}
                className="max-h-44 min-h-[72px] resize-none border-0 bg-transparent px-2 text-base text-white shadow-none placeholder:text-white/30 focus-visible:ring-0"
                placeholder="Ask a question. The agent will retrieve, grade, answer, and cite..."
              />
              <Button type="button" size="icon-lg" className="rounded-full" onClick={onSubmit} disabled={isPending || !value.trim()}>
                {isPending ? <Loader2 className="animate-spin" /> : <Send />}
                <span className="sr-only">Ask Agent</span>
              </Button>
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
}

function SessionsDrawer({
  open,
  onOpenChange,
  sessions,
  activeSessionId,
  onSelect,
  onNew,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  sessions: SessionSummary[];
  activeSessionId: string | null;
  onSelect: (sessionId: string) => void | Promise<void>;
  onNew: () => void;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="left" className="w-full border-white/10 bg-[rgba(3,7,20,0.78)] p-0 text-white backdrop-blur-2xl sm:max-w-md">
        <SheetHeader className="border-b border-white/8 p-6">
          <SheetTitle>Sessions</SheetTitle>
          <SheetDescription>Conversation memory for the course agent.</SheetDescription>
        </SheetHeader>
        <div className="p-5">
          <Button
            type="button"
            className="w-full rounded-full"
            onClick={() => {
              onNew();
              onOpenChange(false);
            }}
          >
            <Plus data-icon="inline-start" />
            New session
          </Button>
        </div>
        <ScrollArea className="h-[calc(100dvh-10rem)] px-5 pb-5">
          <div className="flex flex-col gap-2">
            {sessions.map((session) => (
              <button
                key={session.id}
                type="button"
                onClick={() => {
                  onSelect(session.id);
                  onOpenChange(false);
                }}
                className={cn(
                  "rounded-2xl border px-4 py-3 text-left transition",
                  session.id === activeSessionId ? "border-cyan-200/28 bg-cyan-300/[0.075]" : "border-white/7 bg-white/[0.025] hover:border-cyan-200/22",
                )}
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="min-w-0 truncate text-sm font-medium text-white">{session.title ?? "Untitled session"}</span>
                  <ChevronRight className="text-white/35" />
                </div>
                {session.last_question ? <p className="mt-2 line-clamp-2 text-xs leading-5 text-white/45">{session.last_question}</p> : null}
              </button>
            ))}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}

function TraceTimeline({ trace, isRunning }: { trace: AgentTraceEventPayload[]; isRunning: boolean }) {
  const fallbackSteps = ["query_analyzer", "router", "query_rewriter", "retrievers", "document_grader", "answer_generator", "citation_checker"];
  const steps: AgentTraceEventPayload[] = trace.length
    ? trace
    : fallbackSteps.map((node) => ({ node, status: "pending", document_ids: [], scores: {}, duration_ms: 0 }));
  return (
    <div className="relative flex flex-col gap-3">
      <div className="absolute bottom-4 left-[15px] top-4 w-px bg-gradient-to-b from-cyan-200/30 via-white/12 to-violet-200/20" />
      {steps.map((event, index) => {
        const active = isRunning && index === steps.length - 1;
        return (
          <motion.div key={`${event.node}-${index}`} initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} className="relative flex gap-3">
            <div className={cn("z-10 mt-1 grid size-8 place-items-center rounded-full border bg-[#081126]", active ? "border-cyan-200/50 text-cyan-100 shadow-[0_0_22px_rgba(86,217,255,0.18)]" : "border-white/12 text-white/50")}>
              {active ? <span className="tech-dot" /> : <CircleDot />}
            </div>
            <div className={cn("flex-1 rounded-2xl border p-4", active ? "kg-shimmer border-cyan-200/18 bg-cyan-300/[0.045]" : "border-white/8 bg-white/[0.025]")}>
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-medium text-white">{event.node}</p>
                <span className="font-mono text-xs text-white/38">{event.duration_ms}ms</span>
              </div>
              {event.output_summary ? <MarkdownRenderer content={event.output_summary} compact className="mt-2 line-clamp-3 text-white/55" /> : null}
              {event.document_ids.length ? <p className="mt-2 text-xs text-cyan-100/48">{event.document_ids.length} chunks touched</p> : null}
            </div>
          </motion.div>
        );
      })}
    </div>
  );
}

function TraceDrawer({
  open,
  onOpenChange,
  trace,
  isRunning,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  trace: AgentTraceEventPayload[];
  isRunning: boolean;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full border-white/10 bg-[rgba(3,7,20,0.78)] p-0 text-white backdrop-blur-2xl sm:max-w-xl">
        <SheetHeader className="border-b border-white/8 p-6">
          <SheetTitle>Agent Trace</SheetTitle>
          <SheetDescription>Routing, retrieval, grading, generation, and citation checks.</SheetDescription>
        </SheetHeader>
        <ScrollArea className="h-[calc(100dvh-8rem)] p-6">
          <TraceTimeline trace={trace} isRunning={isRunning} />
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}

function CitationsDrawer({
  open,
  onOpenChange,
  citations,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  citations: Citation[];
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full border-white/10 bg-[rgba(3,7,20,0.78)] p-0 text-white backdrop-blur-2xl sm:max-w-xl">
        <SheetHeader className="border-b border-white/8 p-6">
          <SheetTitle>Citations</SheetTitle>
          <SheetDescription>Verified evidence cards used by the agent answer.</SheetDescription>
        </SheetHeader>
        <ScrollArea className="h-[calc(100dvh-8rem)] p-6">
          <div className="flex flex-col gap-3">
            {citations.length === 0 ? (
              <div className="kg-glass-line rounded-3xl px-6 py-10 text-center text-sm text-white/55">
                <Archive className="mx-auto mb-4 text-cyan-100/70" />
                Citations will appear after a grounded answer completes.
              </div>
            ) : (
              citations.map((citation, index) => <CitationCard key={`${citation.chunk_id}-${index}`} citation={citation} index={index} />)
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}

function QAWorkspaceContent({ selectedCourseId }: { selectedCourseId: string | null }) {
  const queryClient = useQueryClient();
  const storageScope = selectedCourseId ?? "unassigned";
  const dashboardQuery = useQuery({
    queryKey: ["dashboard", selectedCourseId],
    queryFn: () => fetchDashboard(selectedCourseId),
    enabled: Boolean(selectedCourseId),
  });
  const sessionsQuery = useQuery({
    queryKey: ["sessions", selectedCourseId],
    queryFn: () => fetchSessions(selectedCourseId),
    enabled: Boolean(selectedCourseId),
  });
  const [question, setQuestion] = useLocalStorage(`qa.question.${storageScope}`, "");
  const [activeSessionId, setActiveSessionId] = useLocalStorage<string | null>(`qa.sessionId.${storageScope}`, null);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [draftAnswer, setDraftAnswer] = useState("");
  const [citations, setCitations] = useState<Citation[]>([]);
  const [trace, setTrace] = useState<AgentTraceEventPayload[]>([]);
  const [latestRun, setLatestRun] = useState<AgentResponse | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [traceOpen, setTraceOpen] = useState(false);
  const [citationsOpen, setCitationsOpen] = useState(false);

  const askMutation = useMutation({
    mutationFn: async () => {
      const nextQuestion = question.trim();
      if (!nextQuestion) {
        return;
      }
      setStreamError(null);
      setDraftAnswer("");
      setCitations([]);
      setTrace([]);
      setLatestRun(null);
      setTraceOpen(true);
      setTurns((current) => [...current, { role: "user", content: nextQuestion }]);
      setQuestion("");
      await streamAnswer(
        { question: nextQuestion, session_id: activeSessionId, course_id: selectedCourseId, top_k: 6 },
        {
          onTrace: (event) => setTrace((current) => [...current, event]),
          onToken: (token) => setDraftAnswer((current) => `${current}${current ? "\n" : ""}${token}`),
          onCitations: (next) => setCitations(next),
          onFinal: (response) => {
            setLatestRun(response);
            setActiveSessionId(response.session_id);
            setCitations(response.citations);
            setTurns((current) => [
              ...current,
              {
                role: "assistant",
                content: response.answer,
                run_id: response.run_id,
                route: response.route,
                citations: response.citations,
              },
            ]);
            void queryClient.invalidateQueries({ queryKey: ["sessions", selectedCourseId] });
            void queryClient.invalidateQueries({ queryKey: ["session-messages", response.session_id] });
          },
          onError: (message) => setStreamError(message),
        },
      );
    },
  });

  const chapterList = useMemo(() => dashboardQuery.data?.tree.map((node) => node.title).join(" / ") ?? "", [dashboardQuery.data]);

  if (dashboardQuery.isLoading) {
    return <LoadingBlock rows={4} />;
  }
  if (dashboardQuery.error) {
    return <ErrorBlock message={(dashboardQuery.error as Error).message} />;
  }

  return (
    <div className="kg-page relative -mx-4 -my-5 min-h-[calc(100dvh-4.25rem)] px-4 pb-52 pt-5 lg:-mx-7 lg:-my-7 lg:px-7 lg:pt-7">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_50%_0%,rgba(86,217,255,0.11),transparent_34%),radial-gradient(circle_at_88%_20%,rgba(124,92,255,0.11),transparent_30%),linear-gradient(rgba(120,180,255,0.026)_1px,transparent_1px),linear-gradient(90deg,rgba(120,180,255,0.023)_1px,transparent_1px)] bg-[size:auto,auto,48px_48px,48px_48px]" />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-64 bg-gradient-to-t from-[#030714] via-[#030714]/88 to-transparent" />
      <div className="relative z-10 flex flex-col gap-7">
        <ChatHeader
          grounded={!dashboardQuery.data?.degraded_mode}
          chapterList={chapterList}
          latestRun={latestRun}
          onOpenSessions={() => setSessionsOpen(true)}
          onOpenTrace={() => setTraceOpen(true)}
          onOpenCitations={() => setCitationsOpen(true)}
          citationsCount={citations.length}
        />

        <main className="mx-auto w-full max-w-6xl">
          {streamError ? <ErrorBlock message={streamError} /> : null}
          <MessageList
            turns={turns}
            isGenerating={askMutation.isPending}
            draftAnswer={draftAnswer}
            onPickSuggestion={setQuestion}
            onOpenCitations={() => setCitationsOpen(true)}
          />
        </main>

        <ChatComposer
          value={question}
          onChange={setQuestion}
          onSubmit={() => askMutation.mutate()}
          isPending={askMutation.isPending}
          activeSessionId={activeSessionId}
        />
      </div>

      <SessionsDrawer
        open={sessionsOpen}
        onOpenChange={setSessionsOpen}
        sessions={sessionsQuery.data ?? []}
        activeSessionId={activeSessionId}
        onSelect={async (sessionId) => {
          setActiveSessionId(sessionId);
          setDraftAnswer("");
          setCitations([]);
          setTrace([]);
          setLatestRun(null);
          const response = await fetchSessionMessages(sessionId);
          const nextTurns = normalizeMessages(response.messages);
          setTurns(nextTurns);
          const latestAssistant = [...nextTurns].reverse().find((turn) => turn.role === "assistant");
          setCitations(latestAssistant?.citations ?? []);
        }}
        onNew={() => {
          setActiveSessionId(null);
          setTurns([]);
          setDraftAnswer("");
          setCitations([]);
          setTrace([]);
          setLatestRun(null);
          setQuestion("");
        }}
      />
      <TraceDrawer open={traceOpen} onOpenChange={setTraceOpen} trace={trace} isRunning={askMutation.isPending} />
      <CitationsDrawer open={citationsOpen} onOpenChange={setCitationsOpen} citations={citations} />
    </div>
  );
}

export function QAWorkspace() {
  const { selectedCourseId } = useCourseContext();
  return <QAWorkspaceContent key={selectedCourseId ?? "unassigned"} selectedCourseId={selectedCourseId} />;
}
