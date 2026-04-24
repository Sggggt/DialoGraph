"use client";

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";

interface MarkdownRendererProps {
  content: string;
  className?: string;
  compact?: boolean;
}

const components: Components = {
  h1: ({ className, ...props }) => <h1 className={cn("mt-5 text-2xl font-semibold leading-tight text-white first:mt-0", className)} {...props} />,
  h2: ({ className, ...props }) => <h2 className={cn("mt-5 text-xl font-semibold leading-tight text-white first:mt-0", className)} {...props} />,
  h3: ({ className, ...props }) => <h3 className={cn("mt-4 text-lg font-semibold leading-tight text-white first:mt-0", className)} {...props} />,
  h4: ({ className, ...props }) => <h4 className={cn("mt-4 text-base font-semibold leading-tight text-white first:mt-0", className)} {...props} />,
  p: ({ className, ...props }) => <p className={cn("my-3 leading-8 first:mt-0 last:mb-0", className)} {...props} />,
  a: ({ className, ...props }) => (
    <a className={cn("text-cyan-100 underline decoration-cyan-200/35 underline-offset-4 transition hover:text-white", className)} target="_blank" rel="noreferrer" {...props} />
  ),
  strong: ({ className, ...props }) => <strong className={cn("font-semibold text-white", className)} {...props} />,
  em: ({ className, ...props }) => <em className={cn("text-cyan-50/85", className)} {...props} />,
  ul: ({ className, ...props }) => <ul className={cn("my-3 list-disc space-y-2 pl-5 marker:text-cyan-200/70", className)} {...props} />,
  ol: ({ className, ...props }) => <ol className={cn("my-3 list-decimal space-y-2 pl-5 marker:text-cyan-200/70", className)} {...props} />,
  li: ({ className, ...props }) => <li className={cn("leading-7", className)} {...props} />,
  blockquote: ({ className, ...props }) => (
    <blockquote className={cn("my-4 border-l border-cyan-200/35 bg-cyan-300/[0.04] py-2 pl-4 text-white/68", className)} {...props} />
  ),
  hr: ({ className, ...props }) => <hr className={cn("my-5 border-white/10", className)} {...props} />,
  code: ({ className, children, ...props }) => {
    const language = /language-(\w+)/.exec(className ?? "");
    if (language) {
      return (
        <code className={cn("block overflow-x-auto rounded-2xl border border-white/10 bg-black/32 p-4 font-mono text-xs leading-6 text-cyan-50/78", className)} {...props}>
          {children}
        </code>
      );
    }
    return (
      <code className={cn("rounded-md border border-white/10 bg-white/[0.06] px-1.5 py-0.5 font-mono text-[0.92em] text-cyan-50", className)} {...props}>
        {children}
      </code>
    );
  },
  pre: ({ className, ...props }) => <pre className={cn("my-4 overflow-x-auto whitespace-pre-wrap", className)} {...props} />,
  table: ({ className, ...props }) => (
    <div className="my-4 overflow-x-auto rounded-2xl border border-white/10">
      <table className={cn("w-full border-collapse text-left text-sm", className)} {...props} />
    </div>
  ),
  th: ({ className, ...props }) => <th className={cn("border-b border-white/10 bg-white/[0.04] px-3 py-2 font-medium text-white", className)} {...props} />,
  td: ({ className, ...props }) => <td className={cn("border-b border-white/6 px-3 py-2 text-white/68", className)} {...props} />,
};

const compactComponents: Components = {
  ...components,
  h1: ({ className, ...props }) => <span className={cn("font-semibold text-white", className)} {...props} />,
  h2: ({ className, ...props }) => <span className={cn("font-semibold text-white", className)} {...props} />,
  h3: ({ className, ...props }) => <span className={cn("font-semibold text-white", className)} {...props} />,
  p: ({ className, ...props }) => <span className={cn("inline leading-7", className)} {...props} />,
  ul: ({ className, ...props }) => <span className={cn("inline", className)} {...props} />,
  ol: ({ className, ...props }) => <span className={cn("inline", className)} {...props} />,
  li: ({ className, ...props }) => <span className={cn("inline", className)} {...props} />,
  blockquote: ({ className, ...props }) => <span className={cn("inline", className)} {...props} />,
  pre: ({ className, ...props }) => <span className={cn("inline", className)} {...props} />,
  table: ({ className, ...props }) => <span className={cn("inline", className)} {...props} />,
  tr: ({ className, ...props }) => <span className={cn("inline", className)} {...props} />,
  th: ({ className, ...props }) => <span className={cn("inline font-medium", className)} {...props} />,
  td: ({ className, ...props }) => <span className={cn("inline", className)} {...props} />,
};

export function MarkdownRenderer({ content, className, compact = false }: MarkdownRendererProps) {
  return (
    <div className={cn("markdown-output text-sm text-white/72", compact && "leading-7", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={compact ? compactComponents : components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
