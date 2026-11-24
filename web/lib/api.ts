// Single place for fetch calls. Types mirror api/schemas.py (API section).

export type Level = "beginner" | "interview" | "deep";
export type Language = "en" | "hi" | "both";
export type JobStatus =
  | "queued"
  | "searching"
  | "fetching"
  | "tagging"
  | "optimizing"
  | "done"
  | "failed";
export type Depth = 1 | 2 | 3;
export type UncoveredReason = "no candidates found" | "below required depth" | "over budget";

export interface PlanCreate {
  text: string;
  level: Level;
  language: Language;
  budget_hours?: number | null;
}
export interface TopicOut {
  topic_id: string;
  name: string;
}
export interface ModuleOut {
  name: string;
  topics: TopicOut[];
}
export interface PlanOut {
  plan_id: string;
  title: string;
  level: Level;
  language: Language;
  budget_hours: number | null;
  modules: ModuleOut[];
}
export interface TopicIn {
  topic_id?: string | null;
  name: string;
}
export interface ModuleIn {
  name: string;
  topics: TopicIn[];
}
export interface TopicsUpdate {
  modules: ModuleIn[];
}
export interface RunOut {
  job_id: string;
}
export interface JobOut {
  job_id: string;
  plan_id: string;
  status: JobStatus;
  progress: number;
  message: string;
  quota_units: number;
  llm_tokens: number;
}
export interface TopicRef {
  topic_id: string;
  name: string;
  depth: Depth;
}
export interface ResultItem {
  video_id: string;
  title: string;
  channel: string;
  thumbnail: string;
  start_s: number;
  end_s: number;
  url: string;
  topics: TopicRef[];
  reason: string;
}
export interface UncoveredTopic {
  topic_id: string;
  name: string;
  module: string;
  reason: UncoveredReason;
}
export interface Coverage {
  covered: number;
  total: number;
}
export interface ResultOut {
  items: ResultItem[];
  uncovered: UncoveredTopic[];
  total_minutes: number;
  coverage: Coverage;
}
export interface HealthOut {
  ok: boolean;
  quota_used_today: number;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function detailToMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((d) => {
      if (d && typeof d === "object" && "msg" in d) {
        const loc = "loc" in d && Array.isArray(d.loc) ? (d.loc as unknown[]).filter((p) => p !== "body").join(".") : "";
        const msg = String((d as { msg: unknown }).msg);
        return loc ? `${loc}: ${msg}` : msg;
      }
      return String(d);
    });
    if (parts.length) return parts.join("; ");
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Is the API running?");
  }
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = ((await res.json()) as { detail?: unknown }).detail;
    } catch {
      detail = undefined;
    }
    throw new ApiError(res.status, detailToMessage(detail, `Request failed (${res.status})`));
  }
  return (await res.json()) as T;
}

export const createPlan = (body: PlanCreate) =>
  request<PlanOut>("/plans", { method: "POST", body: JSON.stringify(body) });
export const getPlan = (id: string) => request<PlanOut>(`/plans/${encodeURIComponent(id)}`);
export const updateTopics = (id: string, body: TopicsUpdate) =>
  request<{ ok: boolean }>(`/plans/${encodeURIComponent(id)}/topics`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
export const runPlan = (id: string) =>
  request<RunOut>(`/plans/${encodeURIComponent(id)}/run`, { method: "POST" });
export const getJob = (id: string) => request<JobOut>(`/jobs/${encodeURIComponent(id)}`);
export const getResult = (id: string) =>
  request<ResultOut>(`/plans/${encodeURIComponent(id)}/result`);
