import { ReactNode } from "react";

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-col gap-4 border-b pb-5 md:flex-row md:items-end md:justify-between">
      <div className="flex max-w-3xl flex-col gap-2">
        {eyebrow ? <p className="text-xs uppercase tracking-[0.16em] text-muted-foreground">{eyebrow}</p> : null}
        <div className="flex flex-col gap-1">
          <h2 className="text-3xl font-semibold text-foreground">{title}</h2>
          <p className="text-sm leading-6 text-muted-foreground">{description}</p>
        </div>
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </header>
  );
}

