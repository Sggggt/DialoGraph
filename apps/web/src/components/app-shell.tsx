"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import { BookOpenText, BrainCircuit, FolderPlus, Home, Search, Share2, Sparkles, TerminalSquare, Upload } from "lucide-react";

import { AmbientCanvas } from "@/components/ambient-canvas";
import { useCourseContext } from "@/components/course-context";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

const navigation = [
  { href: "/", label: "Overview", caption: "Home", icon: Home },
  { href: "/upload", label: "Ingest", caption: "Ingest", icon: Upload },
  { href: "/search", label: "Search", caption: "Search", icon: Search },
  { href: "/qa", label: "Answer", caption: "Chat", icon: BrainCircuit },
  { href: "/concepts", label: "Concepts", caption: "Concepts", icon: BookOpenText },
  { href: "/graph", label: "Graph", caption: "Graph", icon: Share2 },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { courses, selectedCourse, selectedCourseId, setSelectedCourseId, createCourseSpace, isCreating } = useCourseContext();
  const [createOpen, setCreateOpen] = useState(false);
  const [nextCourseName, setNextCourseName] = useState("");

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
        <header className="sticky top-0 z-30 border-b border-white/6 bg-[rgba(3,7,20,0.36)] backdrop-blur-2xl">
          <div className="flex flex-wrap items-center justify-between gap-4 px-5 py-3 lg:px-7">
            <div className="min-w-0">
              <p className="text-[10px] uppercase tracking-[0.34em] text-cyan-100/42">Course Knowledge Base</p>
              <h1 className="mt-1 break-words text-lg font-semibold text-white lg:text-xl">General Course Intelligence Surface</h1>
              <p className="mt-1 text-xs text-white/45">{selectedCourse?.name ?? "Select a course workspace"}</p>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2">
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
                      No course yet
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
                New Course
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

        <main className="px-4 py-5 lg:px-7 lg:py-7">
          <div className="flex w-full flex-col gap-8">{children}</div>
        </main>
      </div>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-md border border-white/10 bg-[rgba(3,7,20,0.88)] p-0 text-white shadow-[0_30px_80px_rgba(0,0,0,0.4)] backdrop-blur-2xl">
          <DialogHeader className="border-b border-white/8 px-6 py-5">
            <DialogTitle>Create Course Workspace</DialogTitle>
            <DialogDescription>Each course gets its own folder under `data/course-name/` and its own dashboard, graph, search, and chat context.</DialogDescription>
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
              <span className="text-xs uppercase tracking-[0.24em] text-cyan-100/46">Course Name</span>
              <Input
                value={nextCourseName}
                onChange={(event) => setNextCourseName(event.target.value)}
                placeholder="Linear Algebra"
                className="h-12 rounded-2xl border-white/10 bg-white/[0.04] px-4 text-white placeholder:text-white/28"
              />
            </label>
            <div className="flex items-center justify-end gap-2">
              <Button type="button" variant="outline" className="rounded-full" onClick={() => setCreateOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" className="rounded-full" disabled={isCreating || !nextCourseName.trim()}>
                <FolderPlus data-icon="inline-start" />
                {isCreating ? "Creating" : "Create"}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
