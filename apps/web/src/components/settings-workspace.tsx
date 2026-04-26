"use client";

import { useEffect, useState } from "react";
import type { ModelSettingsUpdate } from "@course-kg/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, EyeOff, KeyRound, Loader2, PencilLine, RotateCcw, Save, ServerCog, ShieldAlert } from "lucide-react";

import { ErrorBlock, LoadingBlock } from "@/components/query-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetchModelSettings, updateModelSettings } from "@/lib/api";

type SettingsForm = {
  base_url: string;
  resolve_ip: string;
  embedding_model: string;
  chat_model: string;
  embedding_dimensions: string;
  api_key: string;
  clear_api_key: boolean;
};

export function SettingsWorkspace() {
  const queryClient = useQueryClient();
  const settingsQuery = useQuery({ queryKey: ["model-settings"], queryFn: fetchModelSettings });
  const [form, setForm] = useState<SettingsForm | null>(null);
  const [saved, setSaved] = useState(false);
  const [apiKeyEditing, setApiKeyEditing] = useState(false);

  useEffect(() => {
    if (!settingsQuery.data) {
      return;
    }
    setForm({
      base_url: settingsQuery.data.base_url,
      resolve_ip: settingsQuery.data.resolve_ip ?? "",
      embedding_model: settingsQuery.data.embedding_model,
      chat_model: settingsQuery.data.chat_model,
      embedding_dimensions: String(settingsQuery.data.embedding_dimensions),
      api_key: "",
      clear_api_key: false,
    });
    setApiKeyEditing(false);
  }, [settingsQuery.data]);

  const saveMutation = useMutation({
    mutationFn: (payload: ModelSettingsUpdate) => updateModelSettings(payload),
    onSuccess: async () => {
      setApiKeyEditing(false);
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
  const showApiKeyMask = Boolean(settings?.has_api_key && !apiKeyEditing && !form.clear_api_key);
  const updateForm = <K extends keyof SettingsForm>(key: K, value: SettingsForm[K]) => {
    setForm((current) => (current ? { ...current, [key]: value } : current));
  };

  const handleSubmit = () => {
    const dimensions = Number.parseInt(form.embedding_dimensions, 10);
    saveMutation.mutate({
      base_url: form.base_url.trim(),
      resolve_ip: form.resolve_ip.trim() || null,
      embedding_model: form.embedding_model.trim(),
      chat_model: form.chat_model.trim(),
      embedding_dimensions: Number.isFinite(dimensions) ? dimensions : undefined,
      api_key: form.api_key.trim() || null,
      clear_api_key: form.clear_api_key,
    });
  };

  return (
    <div className="kg-page">
      <section className="glass-panel relative overflow-hidden rounded-[34px] p-6 lg:p-8">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_0%,rgba(86,217,255,0.12),transparent_32%),radial-gradient(circle_at_88%_20%,rgba(124,92,255,0.10),transparent_28%)]" />
        <div className="relative z-10 grid gap-7 xl:grid-cols-[minmax(320px,0.72fr)_minmax(520px,1.28fr)]">
          <div className="space-y-6">
            <div>
              <p className="section-kicker">模型 API</p>
              <h2 className="glow-text mt-2 text-4xl font-semibold text-white">通用模型接口设置</h2>
              <p className="mt-4 max-w-xl text-sm leading-7 text-cyan-50/62">
                这里配置 OpenAI-compatible 接口。可以使用 OpenAI、阿里兼容模式、Kimi 兼容模式或任何实现相同协议的服务。
                保存后会写入项目根目录 .env，并刷新后端运行时配置。
              </p>
            </div>

            <div className="grid gap-3">
              <div className="border-l border-white/10 px-4 py-3">
                <p className="text-xs uppercase tracking-[0.26em] text-white/42">当前状态</p>
                <p className="mt-2 text-xl font-semibold text-white">{settings?.degraded_mode ? "未就绪" : "外部 API"}</p>
              </div>
              <div className="border-l border-white/10 px-4 py-3">
                <p className="text-xs uppercase tracking-[0.26em] text-white/42">API Key</p>
                <p className="mt-2 flex items-center gap-2 text-sm text-white/68">
                  {settings?.has_api_key ? <CheckCircle2 className="size-4 text-emerald-200" /> : <ShieldAlert className="size-4 text-amber-200" />}
                  {settings?.has_api_key ? "已配置" : "未配置"}
                </p>
              </div>
              <div className="border-l border-white/10 px-4 py-3">
                <p className="text-xs uppercase tracking-[0.26em] text-white/42">Provider</p>
                <p className="mt-2 text-sm text-white/68">OpenAI Compatible</p>
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
                  value={form.base_url}
                  onChange={(event) => updateForm("base_url", event.target.value)}
                  placeholder="例如 https://api.openai.com/v1 或 https://dashscope.aliyuncs.com/compatible-mode/v1"
                  className="h-12 rounded-2xl border-white/10 bg-white/[0.04] px-4 text-white placeholder:text-white/28"
                />
              </label>

              <label className="flex flex-col gap-2 md:col-span-2">
                <span className="text-xs uppercase tracking-[0.24em] text-cyan-100/46">DNS Override IP</span>
                <Input
                  value={form.resolve_ip}
                  onChange={(event) => updateForm("resolve_ip", event.target.value)}
                  placeholder="可选；例如 39.96.198.249，留空使用系统 DNS"
                  className="h-12 rounded-2xl border-white/10 bg-white/[0.04] px-4 text-white placeholder:text-white/28"
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
                    value={showApiKeyMask ? "••••••••••••••••" : form.api_key}
                    readOnly={showApiKeyMask}
                    disabled={form.clear_api_key}
                    onChange={(event) => updateForm("api_key", event.target.value)}
                    placeholder={settings?.has_api_key ? "留空则保留当前 key" : "输入 API key"}
                    className="h-12 min-w-0 flex-1 bg-transparent text-sm text-white outline-none placeholder:text-white/30"
                    autoComplete="off"
                  />
                  {showApiKeyMask ? (
                    <button
                      type="button"
                      onClick={() => {
                        setApiKeyEditing(true);
                        updateForm("api_key", "");
                      }}
                      className="inline-flex items-center gap-1 rounded-full border border-white/8 px-2.5 py-1 text-xs text-white/55 transition hover:border-cyan-200/24 hover:text-cyan-100"
                    >
                      <PencilLine className="size-3.5" />
                      修改
                    </button>
                  ) : null}
                  <EyeOff className="size-4 text-white/32" />
                </div>
              </label>
            </div>

            <div className="grid gap-3">
              <label className="flex items-center gap-3 border-l border-white/10 px-4 py-3 text-sm text-white/70">
                <input
                  type="checkbox"
                  checked={form.clear_api_key}
                  onChange={(event) => {
                    updateForm("clear_api_key", event.target.checked);
                    if (event.target.checked) {
                      setApiKeyEditing(false);
                      updateForm("api_key", "");
                    }
                  }}
                  className="size-4 accent-rose-300"
                />
                清除当前 API key
              </label>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-white/8 pt-5">
              <p className="text-xs leading-6 text-white/42">
                修改向量维度后，已创建的向量库可能需要重建。已运行中的解析批次不会切换模型，新批次会使用这里的配置。
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
          { label: "Base URL", value: settings?.base_url, icon: ServerCog },
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
