"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ApiError, getPlan, runPlan, updateTopics, type ModuleIn, type PlanOut } from "@/lib/api";
import { ErrorBox } from "@/components/ErrorBox";
import { Loading } from "@/components/Loading";
import { btn2Cls, btnCls, inputCls } from "@/components/ui";

const MAX_TOPICS = 40;

export default function ReviewPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [plan, setPlan] = useState<PlanOut | null>(null);
  const [modules, setModules] = useState<ModuleIn[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    getPlan(id)
      .then((p) => {
        if (!alive) return;
        setPlan(p);
        setModules(p.modules.map((m) => ({ name: m.name, topics: m.topics.map((t) => ({ ...t })) })));
      })
      .catch((e: unknown) => alive && setLoadError(e instanceof Error ? e.message : "Failed to load plan."));
    return () => {
      alive = false;
    };
  }, [id]);

  if (loadError) return <ErrorBox message={loadError} />;
  if (!plan) return <Loading label="Loading plan..." />;

  const total = modules.reduce((n, m) => n + m.topics.length, 0);
  const over = total > MAX_TOPICS;
  const valid = !over && total > 0 && modules.every((m) => m.name.trim() && m.topics.length > 0 && m.topics.every((t) => t.name.trim()));

  const setModule = (i: number, fn: (m: ModuleIn) => ModuleIn) =>
    setModules((ms) => ms.map((m, j) => (j === i ? fn(m) : m)));

  async function onFind() {
    setBusy(true);
    setError(null);
    try {
      await updateTopics(id, {
        modules: modules.map((m) => ({
          name: m.name.trim(),
          topics: m.topics.map((t) => ({ topic_id: t.topic_id ?? null, name: t.name.trim() })),
        })),
      });
      const { job_id } = await runPlan(id);
      router.push(`/plans/${id}/run?job=${encodeURIComponent(job_id)}`);
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong.");
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">{plan.title}</h1>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          Review the topics. Level: {plan.level}. Language: {plan.language}.
        </p>
      </div>
      <p className={`text-sm font-medium ${over ? "text-red-700 dark:text-red-400" : ""}`} aria-live="polite">
        {total} / {MAX_TOPICS} topics{over ? " (too many: delete some)" : ""}
      </p>
      {modules.length === 0 && <p className="text-sm">No modules. Add one below.</p>}
      {modules.map((m, i) => (
        <section key={i} className="space-y-2 rounded-md border border-zinc-200 p-3 dark:border-zinc-800">
          <div className="flex gap-2">
            <input
              aria-label={`Module ${i + 1} name`}
              value={m.name}
              onChange={(e) => setModule(i, (x) => ({ ...x, name: e.target.value }))}
              maxLength={200}
              className={`${inputCls} font-medium`}
            />
            <button type="button" className={btn2Cls} onClick={() => setModules((ms) => ms.filter((_, j) => j !== i))}>
              Delete module
            </button>
          </div>
          <ul className="space-y-2 pl-2">
            {m.topics.map((t, k) => (
              <li key={k} className="flex gap-2">
                <input
                  aria-label={`Topic ${k + 1} in module ${i + 1}`}
                  value={t.name}
                  onChange={(e) =>
                    setModule(i, (x) => ({ ...x, topics: x.topics.map((y, z) => (z === k ? { ...y, name: e.target.value } : y)) }))
                  }
                  maxLength={200}
                  className={inputCls}
                />
                <button
                  type="button"
                  aria-label={`Delete topic ${t.name}`}
                  className={btn2Cls}
                  onClick={() => setModule(i, (x) => ({ ...x, topics: x.topics.filter((_, z) => z !== k) }))}
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
          <button
            type="button"
            className={btn2Cls}
            disabled={total >= MAX_TOPICS}
            onClick={() => setModule(i, (x) => ({ ...x, topics: [...x.topics, { topic_id: null, name: "" }] }))}
          >
            Add topic
          </button>
        </section>
      ))}
      <button type="button" className={btn2Cls} onClick={() => setModules((ms) => [...ms, { name: "New module", topics: [{ topic_id: null, name: "" }] }])}>
        Add module
      </button>
      {error && <ErrorBox message={error} />}
      <div>
        <button type="button" className={btnCls} disabled={!valid || busy} onClick={onFind}>
          {busy ? "Starting..." : "Find videos"}
        </button>
        {!valid && !over && <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">Every module needs a name and at least one named topic.</p>}
      </div>
    </div>
  );
}
