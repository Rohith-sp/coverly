"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { ApiError, getJob, type JobOut, type JobStatus } from "@/lib/api";
import { ErrorBox } from "@/components/ErrorBox";
import { Loading } from "@/components/Loading";

const STAGES: JobStatus[] = ["searching", "fetching", "tagging", "optimizing"];

function Run() {
  const { id } = useParams<{ id: string }>();
  const job = useSearchParams().get("job");
  const router = useRouter();
  const [data, setData] = useState<JobOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!job) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const j = await getJob(job);
        if (!alive) return;
        setData(j);
        setError(null);
        if (j.status === "done") {
          router.push(`/plans/${id}`);
          return;
        }
        if (j.status === "failed") return;
      } catch (e) {
        if (!alive) return;
        setError(e instanceof Error ? e.message : "Failed to load job.");
        // 4xx is permanent (bad or unknown job id): stop polling. Network errors and 5xx keep retrying.
        if (e instanceof ApiError && e.status >= 400 && e.status < 500) return;
      }
      timer = setTimeout(tick, 2000);
    };
    tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [job, id, router]);

  if (!job) {
    return (
      <ErrorBox message="No job specified.">
        <Link href={`/plans/${id}/review`} className="underline">
          Back to review
        </Link>
      </ErrorBox>
    );
  }
  if (!data) return error ? <ErrorBox message={error} /> : <Loading label="Starting..." />;

  const stageIdx = STAGES.indexOf(data.status);
  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-semibold">Finding videos</h1>
      <ol className="flex flex-wrap gap-2 text-sm" aria-label="Stages">
        {STAGES.map((s, i) => (
          <li
            key={s}
            aria-current={s === data.status ? "step" : undefined}
            className={`rounded-full border px-3 py-1 ${
              s === data.status
                ? "border-indigo-600 bg-indigo-600 text-white"
                : data.status === "done" || (stageIdx >= 0 && i < stageIdx)
                  ? "border-zinc-400 text-zinc-700 dark:text-zinc-300"
                  : "border-zinc-300 text-zinc-600 dark:border-zinc-700 dark:text-zinc-400"
            }`}
          >
            {s}
          </li>
        ))}
      </ol>
      <div
        role="progressbar"
        aria-valuenow={data.progress}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Progress"
        className="h-3 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800"
      >
        <div className="h-full bg-indigo-600 transition-all" style={{ width: `${data.progress}%` }} />
      </div>
      <p className="text-sm" aria-live="polite">
        {data.progress}% {data.message && `- ${data.message}`}
      </p>
      <p className="text-xs text-zinc-600 dark:text-zinc-400">
        YouTube quota: {data.quota_units} units. LLM tokens: {data.llm_tokens.toLocaleString()}.
      </p>
      {error && <ErrorBox message={error} />}
      {data.status === "failed" && (
        <ErrorBox message={data.message || "The job failed."}>
          <Link href={`/plans/${id}/review`} className="mt-2 inline-block underline">
            Back to review
          </Link>
        </ErrorBox>
      )}
    </div>
  );
}

export default function RunPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Run />
    </Suspense>
  );
}
