"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, createPlan, type Language, type Level } from "@/lib/api";
import { ErrorBox } from "@/components/ErrorBox";
import { btnCls, inputCls } from "@/components/ui";

const MAX_CHARS = 20_000;

export default function NewPlanPage() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [level, setLevel] = useState<Level>("interview");
  const [language, setLanguage] = useState<Language>("en");
  const [budget, setBudget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const hours = budget.trim() === "" ? null : Number(budget);
      const plan = await createPlan({ text, level, language, budget_hours: hours });
      router.push(`/plans/${plan.plan_id}/review`);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 422
          ? `Could not parse the plan: ${err.message}`
          : err instanceof Error
            ? err.message
            : "Something went wrong.",
      );
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-5">
      <h1 className="text-2xl font-semibold">New study plan</h1>
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        Paste your syllabus. Coverly finds the fewest videos that cover every topic. Max 40 topics.
      </p>
      <div>
        <label htmlFor="text" className="mb-1 block text-sm font-medium">
          Study plan
        </label>
        <textarea
          id="text"
          required
          rows={12}
          maxLength={MAX_CHARS}
          value={text}
          onChange={(e) => setText(e.target.value)}
          className={inputCls}
          placeholder="Module 1: Auth&#10;- sessions vs JWT&#10;- refresh tokens"
        />
        <p className="mt-1 text-right text-xs text-zinc-600 dark:text-zinc-400">
          {text.length.toLocaleString()} / {MAX_CHARS.toLocaleString()}
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <div>
          <label htmlFor="level" className="mb-1 block text-sm font-medium">
            Level
          </label>
          <select id="level" value={level} onChange={(e) => setLevel(e.target.value as Level)} className={inputCls}>
            <option value="beginner">Beginner</option>
            <option value="interview">Interview</option>
            <option value="deep">Deep</option>
          </select>
        </div>
        <div>
          <label htmlFor="lang" className="mb-1 block text-sm font-medium">
            Language
          </label>
          <select id="lang" value={language} onChange={(e) => setLanguage(e.target.value as Language)} className={inputCls}>
            <option value="en">English</option>
            <option value="hi">Hindi</option>
            <option value="both">Both</option>
          </select>
        </div>
        <div>
          <label htmlFor="budget" className="mb-1 block text-sm font-medium">
            Budget (hours, optional)
          </label>
          <input
            id="budget"
            type="number"
            min="0.1"
            max="1000"
            step="any"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
            className={inputCls}
          />
        </div>
      </div>
      {error && <ErrorBox message={error} />}
      <button type="submit" disabled={busy || text.trim() === ""} className={btnCls}>
        {busy ? "Parsing..." : "Parse plan"}
      </button>
    </form>
  );
}
