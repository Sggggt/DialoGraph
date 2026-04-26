"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BookOpenText, BrainCircuit, FolderPlus, Home, RefreshCw, Search, Settings, Share2, Sparkles, TerminalSquare, Upload } from "lucide-react";

import { AmbientCanvas } from "@/components/ambient-canvas";
import { useCourseContext } from "@/components/course-context";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { refreshCourse } from "@/lib/api";
import { cn } from "@/lib/utils";

const navigation = [
  { href: "/", label: "概览", caption: "首页", icon: Home },
  { href: "/upload", label: "导入", caption: "导入", icon: Upload },
  { href: "/search", label: "搜索", caption: "搜索", icon: Search },
  { href: "/qa", label: "问答", caption: "对话", icon: BrainCircuit },
  { href: "/concepts", label: "概念", caption: "概念", icon: BookOpenText },
  { href: "/graph", label: "图谱", caption: "图谱", icon: Share2 },
  { href: "/settings", label: "设置", caption: "模型", icon: Settings },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const { courses, selectedCourse, selectedCourseId, setSelectedCourseId, createCourseSpace, isCreating } = useCourseContext();
  const [createOpen, setCreateOpen] = useState(false);
  const [nextCourseName, setNextCourseName] = useState("");
  const [refreshDone, setRefreshDone] = useState(false);
  const refreshMutation = useMutation({
    mutationFn: () => refreshCourse(selectedCourseId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["courses"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard", selectedCourseId] }),
        queryClient.invalidateQueries({ queryKey: ["course-files", selectedCourseId] }),
        queryClient.invalidateQueries({ queryKey: ["graph", selectedCourseId] }),
        queryClient.invalidateQueries({ queryKey: ["chapter-graph"] }),
        queryClient.invalidateQueries({ queryKey: ["graph-node"] }),
        queryClient.invalidateQueries({ queryKey: ["concepts", selectedCourseId] }),
        queryClient.invalidateQueries({ queryKey: ["sessions", selectedCourseId] }),
      ]);
      setRefreshDone(true);
      window.setTimeout(() => setRefreshDone(false), 1600);
    },
  });

  return (
    <div className="kg-future-field relative min-h-screen overflow-x-hidden bg-[#030714] text-foreground">
      <AmbientCanvas />

      <aside className="fixed inset-y-0 left-0 z-40 hidden w-[76px] border-r border-white/7 bg-[rgba(3,7,20,0.55)] backdrop-blur-2xl lg:flex lg:flex-col lg:items-center lg:gap-7 lg:py-6">
        <div className="grid size-11 place-items-center rounded-2xl border border-cyan-300/20 bg-cyan-300/[0.06] text-[10px] font-semibold uppercase tracking-[0.24em] text-cyan-100 shadow-[0_0_24px_rgba(85,215,255,0.08)]">
          KG
        </div>

        <nav className="flex flex-col gap-2">
          {navigation.map(({ href, label, caption, icon: Icon }) => {
            const active = pathname === href;
            return (
              <Link key={href} href={href} className="group relative">
                <motion.div
                  whileHover={{ x: 3, y: -1 }}
                  whileTap={{ scale: 0.97 }}
                  className={cn(
                    "relative grid size-11 place-items-center rounded-2xl border border-transparent text-white/48 transition duration-200",
                    active && "border-cyan-300/25 bg-cyan-300/[0.075] text-white shadow-[0_0_24px_rgba(85,215,255,0.08)]",
                  )}
                >
                  <span
                    className={cn(
                      "absolute inset-y-2 -left-[17px] w-px rounded-full bg-transparent transition",
                      active && "bg-cyan-200/90 shadow-[0_0_12px_rgba(132,221,255,0.35)]",
                    )}
                  />
                  <Icon className="size-5 shrink-0" />
                </motion.div>
                <span className="pointer-events-none absolute left-full top-1/2 ml-3 -translate-y-1/2 rounded-full border border-white/8 bg-[rgba(5,9,24,0.92)] px-3 py-1 text-xs text-white/72 opacity-0 transition group-hover:opacity-100">
                  {caption} / {label}
                </span>
              </Link>
            );
          })}
        </nav>
      </aside>

      <div className="relative min-h-screen lg:pl-[76px]">
        <header className="fixed inset-x-0 top-0 z-30 border-b border-white/6 bg-[rgba(3,7,20,0.78)] backdrop-blur-2xl lg:left-[76px]">
          <div className="flex flex-wrap items-center justify-between gap-4 px-5 py-3 lg:px-7">
            <div className="min-w-0">
              <p className="text-[10px] uppercase tracking-[0.34em] text-cyan-100/42">Course Knowledge Base</p>
              <h1 className="mt-1 break-words text-lg font-semibold text-white lg:text-xl">General Course Intelligence Surface</h1>
              <p className="mt-1 text-xs text-white/45">{selectedCourse?.name ?? "选择课程空间"}</p>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <div className="relative">
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  className="rounded-full"
                  aria-label="刷新当前课程"
                  onClick={() => refreshMutation.mutate()}
                  disabled={!selectedCourseId || refreshMutation.isPending}
                >
                  <RefreshCw className={cn("size-4", refreshMutation.isPending && "animate-spin")} />
                </Button>
                {refreshDone ? (
                  <span className="absolute right-0 top-full z-50 mt-2 whitespace-nowrap rounded-full border border-emerald-200/20 bg-[rgba(4,17,24,0.94)] px-3 py-1 text-xs text-emerald-100 shadow-[0_12px_32px_rgba(0,0,0,0.35)]">
                    已刷新
                  </span>
                ) : null}
              </div>
              <div className="flex items-center gap-2 rounded-full border border-cyan-300/16 bg-cyan-300/[0.06] px-3 py-2 text-xs text-white/75 shadow-[0_0_24px_rgba(85,215,255,0.06)]">
                <BookOpenText className="size-4 text-cyan-100/78" />
                <select
                  value={selectedCourseId ?? ""}
                  onChange={(event) => setSelectedCourseId(event.target.value || null)}
                  disabled={courses.length === 0}
                  className="min-w-[11rem] bg-transparent text-sm text-white outline-none"
                >
                  {courses.length === 0 ? (
                    <option value="" className="bg-[#081126] text-white">
                      暂无课程
                    </option>
                  ) : null}
                  {courses.map((course) => (
                    <option key={course.id} value={course.id} className="bg-[#081126] text-white">
                      {course.name}
                    </option>
                  ))}
                </select>
              </div>
              <Button type="button" variant="outline" className="rounded-full" onClick={() => setCreateOpen(true)}>
                <FolderPlus data-icon="inline-start" />
                新建课程
              </Button>
              <div className="kg-micro-chip rounded-full px-3 py-2 text-xs">
                <Sparkles data-icon="inline-start" />
                Agentic RAG
              </div>
              <div className="kg-micro-chip rounded-full px-3 py-2 text-xs">
                <TerminalSquare data-icon="inline-start" />
                Hybrid Graph Runtime
              </div>
            </div>
          </div>
        </header>

        <main className="px-4 pb-5 pt-[9.5rem] lg:px-7 lg:pb-7 lg:pt-[8.5rem]">
          <div className="flex w-full flex-col gap-8">{children}</div>
        </main>
      </div>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-md border border-white/10 bg-[rgba(3,7,20,0.88)] p-0 text-white shadow-[0_30px_80px_rgba(0,0,0,0.4)] backdrop-blur-2xl">
          <DialogHeader className="border-b border-white/8 px-6 py-5">
            <DialogTitle>新建课程空间</DialogTitle>
            <DialogDescription>创建课程看板、图谱、搜索和问答上下文。课程文件会统一进入本课程 storage 文件夹。</DialogDescription>
          </DialogHeader>
          <form
            className="space-y-4 px-6 py-5"
            onSubmit={async (event) => {
              event.preventDefault();
              const name = nextCourseName.trim();
              if (!name) {
                return;
              }
              await createCourseSpace({ name });
              setNextCourseName("");
              setCreateOpen(false);
            }}
          >
            <label className="flex flex-col gap-2">
              <span className="text-xs uppercase tracking-[0.24em] text-cyan-100/46">课程名称</span>
              <Input
                value={nextCourseName}
                onChange={(event) => setNextCourseName(event.target.value)}
                placeholder="线性代数"
                className="h-12 rounded-2xl border-white/10 bg-white/[0.04] px-4 text-white placeholder:text-white/28"
              />
            </label>
            <div className="flex items-center justify-end gap-2">
              <Button type="button" variant="outline" className="rounded-full" onClick={() => setCreateOpen(false)}>
                取消
              </Button>
              <Button type="submit" className="rounded-full" disabled={isCreating || !nextCourseName.trim()}>
                <FolderPlus data-icon="inline-start" />
                {isCreating ? "创建中" : "创建"}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
