/**
 * Client-side recent-jobs index.
 *
 * The /v1 API deliberately has no job-list endpoint (Phase 1 decision), so
 * the app keeps its own index of submitted jobs in localStorage. It is
 * best-effort: corrupt or unavailable storage degrades to an empty list.
 */

export interface RecentJob {
  id: string;
  createdAt: string;
  filename?: string;
}

const STORAGE_KEY = "stt.recentJobs";
const CAP = 20;

function isRecentJob(value: unknown): value is RecentJob {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.id === "string" &&
    typeof record.createdAt === "string" &&
    (record.filename === undefined || typeof record.filename === "string")
  );
}

export function getRecentJobs(): RecentJob[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isRecentJob);
  } catch {
    return [];
  }
}

export function addRecentJob(job: RecentJob): void {
  const next = [
    job,
    ...getRecentJobs().filter((entry) => entry.id !== job.id),
  ].slice(0, CAP);
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Storage full or denied: the index is best-effort by design.
  }
}
