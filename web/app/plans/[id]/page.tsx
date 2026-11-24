import Link from "next/link";
import { ApiError, getResult, type ResultOut } from "@/lib/api";
import { ErrorBox } from "@/components/ErrorBox";

function fmt(s: number): string {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = String(m).padStart(h > 0 ? 2 : 1, "0");
  const ss = String(sec).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function fmtMinutes(min: number): string {
  const h = Math.floor(min / 60);
  return h > 0 ? `${h} h ${min % 60} min` : `${min} min`;
}

export default async function ResultPage({ params }: PageProps<"/plans/[id]">) {
  const { id } = await params;
  let result: ResultOut;
  try {
    result = await getResult(id);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 409)) {
      return (
        <div className="space-y-3">
          <h1 className="text-2xl font-semibold">Result not ready</h1>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            This plan has no result yet. Start a run from the review page, or check an existing run.
          </p>
          <p className="text-sm">
            <Link href={`/plans/${id}/review`} className="underline">
              Review topics
            </Link>
          </p>
        </div>
      );
    }
    return <ErrorBox message={e instanceof Error ? e.message : "Failed to load result."} />;
  }

  const { items, uncovered, coverage, total_minutes } = result;
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Your watch plan</h1>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          Total watch time {fmtMinutes(total_minutes)}. Topics covered {coverage.covered}/{coverage.total}.
        </p>
      </div>
      {items.length === 0 ? (
        <p className="rounded-md border border-zinc-200 p-4 text-sm dark:border-zinc-800">
          No videos found for {uncovered.length} topic{uncovered.length === 1 ? "" : "s"}.
        </p>
      ) : (
        <ol className="space-y-4">
          {items.map((it, i) => (
            <li key={`${it.video_id}-${it.start_s}`} className="flex flex-col gap-3 rounded-md border border-zinc-200 p-3 sm:flex-row dark:border-zinc-800">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={it.thumbnail} alt="" width={160} height={90} loading="lazy" className="h-[90px] w-[160px] shrink-0 rounded object-cover" />
              <div className="min-w-0 space-y-1">
                <a
                  href={it.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium underline focus-visible:outline-2 focus-visible:outline-indigo-500"
                >
                  {i + 1}. {it.title}
                </a>
                <p className="text-sm text-zinc-600 dark:text-zinc-400">
                  {it.channel} - {fmt(it.start_s)} to {fmt(it.end_s)}
                </p>
                <p className="text-sm">{it.reason}</p>
                <ul className="flex flex-wrap gap-1.5 pt-1">
                  {it.topics.map((t) => (
                    <li key={t.topic_id} className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs dark:bg-zinc-800">
                      {t.name} (depth {t.depth})
                    </li>
                  ))}
                </ul>
              </div>
            </li>
          ))}
        </ol>
      )}
      {uncovered.length > 0 && (
        <section>
          <h2 className="mb-2 text-lg font-semibold">Uncovered topics</h2>
          <ul className="space-y-1 text-sm">
            {uncovered.map((u) => (
              <li key={u.topic_id}>
                {u.name} <span className="text-zinc-600 dark:text-zinc-400">({u.module}) - {u.reason}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
