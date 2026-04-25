"use client";

import { useEffect, useState } from "react";
import type { ModelSettingsUpdate } from "@course-kg/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, EyeOff, KeyRound, Loader2, RotateCcw, Save, ServerCog, ShieldAlert } from "lucide-react";

import { ErrorBlock, LoadingBlock } from "@/components/query-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetchModelSettings, updateModelSettings } from "@/lib/api";

type SettingsForm = {
  dashscope_base_url: string;
  embedding_model: string;
  chat_model: string;
  embedding_dimensions: string;
  dashscope_api_key: string;
  clear_dashscope_api_key: boolean;
  enable_fake_embeddings: boolean;
  enable_fake_chat: boolean;
};

export function SettingsWorkspace() {
  const queryClient = useQueryClient();
  const settingsQuery = useQuery({ queryKey: ["model-settings"], queryFn: fetchModelSettings });
  const [form, setForm] = useState<SettingsForm | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!settingsQuery.data) {
      return;
    }
    setForm({
      dashscope_base_url: settingsQuery.data.dashscope_base_url,
      embedding_model: settingsQuery.data.embedding_model,
      chat_model: settingsQuery.data.chat_model,
      embedding_dimensions: String(settingsQuery.data.embedding_dimensions),
      dashscope_api_key: "",
      clear_dashscope_api_key: false,
      enable_fake_embeddings: settingsQuery.data.enable_fake_embeddings,
      enable_fake_chat: settingsQuery.data.enable_fake_chat,
    });
  }, [settingsQuery.data]);

  const saveMutation = useMutation({
    mutationFn: (payload: ModelSettingsUpdate) => updateModelSettings(payload),
    onSuccess: async () => {
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1800);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["model-settings"] }),
        queryClient.invalidateQueries({ queryKey: ["courses"] }),
      ]);
    },
  });

  if (settingsQuery.isLoading || !form) {
    return <LoadingBlock rows={4} />;
  }
  if (settingsQuery.error) {
    return <ErrorBlock message={(settingsQuery.error as Error).message} />;
  }

  const settings = settingsQuery.data;
  const updateForm = <K extends keyof SettingsForm>(key: K, value: SettingsForm[K]) => {
    setForm((current) => (current ? { ...current, [key]: value } : current));
  };

  const handleSubmit = () => {
    const dimensions = Number.parseInt(form.embedding_dimensions, 10);
    saveMutation.mutate({
      dashscope_base_url: form.dashscope_base_url.trim(),
      embedding_model: form.embedding_model.trim(),
      chat_model: form.chat_model.trim(),
      embedding_dimensions: Number.isFinite(dimensions) ? dimensions : undefined,
      dashscope_api_key: form.dashscope_api_key.trim() || null,
      clear_dashscope_api_key: form.clear_dashscope_api_key,
      enable_fake_embeddings: form.enable_fake_embeddings,
      enable_fake_chat: form.enable_fake_chat,
    });
  };

  return (
    <div className="kg-page">
      <section className="glass-panel relative overflow-hidden rounded-[34px] p-6 lg:p-8">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_0%,rgba(86,217,255,0.12),transparent_32%),radial-gradient(circle_at_88%_20%,rgba(124,92,255,0.10),transparent_28%)]" />
        <div className="relative z-10 grid gap-7 xl:grid-cols-[minmax(320px,0.72fr)_minmax(520px,1.28fr)]">
          <div className="space-y-6">
            <div>
              <p className="section-kicker">Model API</p>
              <h2 className="glow-text mt-2 text-4xl font-semibold text-white">模型与 API 设置</h2>
              <p className="mt-4 max-w-xl text-sm leading-7 text-cyan-50/62">
                修改会写入项目根目录 .env，并立即刷新后端运行时配置。已经运行中的解析批次不会切换模型，新批次会使用这里的新设置。
              </p>
            </div>

            <div className="grid gap-3">
              <div className="border-l border-white/10 px-4 py-3">
                <p className="text-xs uppercase tracking-[0.26em] text-white/42">当前状态</p>
                <p className="mt-2 text-xl font-semibold text-white">{settings?.degraded_mode ? "Fallback / Fake" : "外部 API"}</p>
              </div>
              <div className="border-l border-white/10 px-4 py-3">
                <p className="text-xs uppercase tracking-[0.26em] text-white/42">API Key</p>
                <p className="mt-2 flex items-center gap-2 text-sm text-white/68">
                  {settings?.has_dashscope_api_key ? <CheckCircle2 className="size-4 text-emerald-200" /> : <ShieldAlert className="size-4 text-amber-200" />}
                  {settings?.has_dashscope_api_key ? "已配置" : "未配置"}
                </p>
              </div>
              <div className="border-l border-white/10 px-4 py-3">
                <p className="text-xs uppercase tracking-[0.26em] text-white/42">Provider</p>
                <p className="mt-2 text-sm text-white/68">DashScope compatible</p>
              </div>
            </div>
          </div>

          <form
            className="relative z-10 grid gap-5"
            onSubmit={(event) => {
              event.preventDefault();
              handleSubmit();
            }}
          >
            <div className="grid gap-5 md:grid-cols-2">
              <label className="flex flex-col gap-2 md:col-span-2">
                <span className="text-xs uppercase tracking-[0.24em] text-cyan-100/46">Base URL</span>
                <Input
                  value={form.dashscope_base_url}
                  onChange={(event) => updateForm("dashscope_base_url", event.target.value)}
                  className="h-12 rounded-2xl border-white/10 bg-white/[0.04] px-4 text-white"
                />
              </label>

              <label className="flex flex-col gap-2">
                <span className="text-xs uppercase tracking-[0.24em] text-cyan-100/46">Embedding 模型</span>
                <Input
                  value={form.embedding_model}
                  onChange={(event) => updateForm("embedding_model", event.target.value)}
                  className="h-12 rounded-2xl border-white/10 bg-white/[0.04] px-4 text-white"
                />
              </label>

              <label className="flex flex-col gap-2">
                <span className="text-xs uppercase tracking-[0.24em] text-cyan-100/46">Chat / 图谱模型</span>
                <Input
                  value={form.chat_model}
                  onChange={(event) => updateForm("chat_model", event.target.value)}
                  className="h-12 rounded-2xl border-white/10 bg-white/[0.04] px-4 text-white"
                />
              </label>

              <label className="flex flex-col gap-2">
                <span className="text-xs uppercase tracking-[0.24em] text-cyan-100/46">向量维度</span>
                <Input
                  type="number"
                  min={1}
                  max={8192}
                  value={form.embedding_dimensions}
                  onChange={(event) => updateForm("embedding_dimensions", event.target.value)}
                  className="h-12 rounded-2xl border-white/10 bg-white/[0.04] px-4 text-white"
                />
              </label>

              <label className="flex flex-col gap-2">
                <span className="text-xs uppercase tracking-[0.24em] text-cyan-100/46">API Key</span>
                <div className="flex items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.04] px-4">
                  <KeyRound className="size-4 text-cyan-100/58" />
                  <input
                    type="password"
                    value={form.dashscope_api_key}
                    onChange={(event) => updateForm("dashscope_api_key", event.target.value)}
                    placeholder={settings?.has_dashscope_api_key ? "留空则保留当前 key" : "输入 API key"}
                    className="h-12 min-w-0 flex-1 bg-transparent text-sm text-white outline-none placeholder:text-white/30"
                    autoComplete="off"
                  />
                  <EyeOff className="size-4 text-white/32" />
                </div>
              </label>
            </div>

            <div className="grid gap-3 md:grid-cols-3">
              <label className="flex items-center gap-3 border-l border-white/10 px-4 py-3 text-sm text-white/70">
                <input
                  type="checkbox"
                  checked={form.enable_fake_embeddings}
                  onChange={(event) => updateForm("enable_fake_embeddings", event.target.checked)}
                  className="size-4 accent-cyan-300"
                />
                Embedding 使用 fake fallback
              </label>
              <label className="flex items-center gap-3 border-l border-white/10 px-4 py-3 text-sm text-white/70">
                <input
                  type="checkbox"
                  checked={form.enable_fake_chat}
                  onChange={(event) => updateForm("enable_fake_chat", event.target.checked)}
                  className="size-4 accent-cyan-300"
                />
                Chat / 图谱使用 heuristic
              </label>
              <label className="flex items-center gap-3 border-l border-white/10 px-4 py-3 text-sm text-white/70">
                <input
                  type="checkbox"
                  checked={form.clear_dashscope_api_key}
                  onChange={(event) => updateForm("clear_dashscope_api_key", event.target.checked)}
                  className="size-4 accent-rose-300"
                />
                清除当前 API key
              </label>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-white/8 pt-5">
              <p className="text-xs leading-6 text-white/42">
                保存后建议重新启动解析批次。Qdrant 集合维度已创建时，修改向量维度可能需要重建向量库。
              </p>
              <div className="flex items-center gap-2">
                {saved ? <span className="text-sm text-emerald-100">已保存</span> : null}
                <Button type="button" variant="outline" className="rounded-full" onClick={() => settingsQuery.refetch()}>
                  <RotateCcw data-icon="inline-start" />
                  重新载入
                </Button>
                <Button type="submit" className="rounded-full" disabled={saveMutation.isPending}>
                  {saveMutation.isPending ? <Loader2 data-icon="inline-start" className="animate-spin" /> : <Save data-icon="inline-start" />}
                  保存设置
                </Button>
              </div>
            </div>
          </form>
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-3">
        {[
          { label: "Embedding", value: settings?.embedding_model, icon: ServerCog },
          { label: "Chat / Graph", value: settings?.chat_model, icon: ServerCog },
          { label: "Base URL", value: settings?.dashscope_base_url, icon: ServerCog },
        ].map(({ label, value, icon: Icon }) => (
          <div key={label} className="glass-panel rounded-[24px] p-5">
            <div className="flex items-center gap-3">
              <Icon className="size-4 text-cyan-100/70" />
              <p className="text-xs uppercase tracking-[0.24em] text-white/42">{label}</p>
            </div>
            <p className="mt-3 break-all text-sm text-white/72">{value}</p>
          </div>
        ))}
      </section>
    </div>
  );
}
