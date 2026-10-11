import { addRecentJob, getRecentJobs } from "./recentJobs";

afterEach(() => {
  localStorage.clear();
});

test("add then get round-trips a job", () => {
  addRecentJob({ id: "j-1", createdAt: "2026-10-11T10:00:00Z", filename: "a.wav" });

  const jobs = getRecentJobs();

  expect(jobs).toHaveLength(1);
  expect(jobs[0]).toMatchObject({ id: "j-1", filename: "a.wav" });
});

test("re-adding a job moves it to the front without duplicating", () => {
  addRecentJob({ id: "j-1", createdAt: "2026-10-11T10:00:00Z" });
  addRecentJob({ id: "j-2", createdAt: "2026-10-11T10:01:00Z" });
  addRecentJob({ id: "j-1", createdAt: "2026-10-11T10:02:00Z" });

  const ids = getRecentJobs().map((job) => job.id);

  expect(ids).toEqual(["j-1", "j-2"]);
});

test("the index is capped at 20 entries", () => {
  for (let i = 0; i < 25; i += 1) {
    addRecentJob({ id: `j-${i}`, createdAt: "2026-10-11T10:00:00Z" });
  }

  const jobs = getRecentJobs();

  expect(jobs).toHaveLength(20);
  expect(jobs[0].id).toBe("j-24");
});

test("corrupt localStorage degrades to an empty index", () => {
  localStorage.setItem("stt.recentJobs", "{not json");

  expect(getRecentJobs()).toEqual([]);
});

test("non-array storage value degrades to an empty index", () => {
  localStorage.setItem("stt.recentJobs", JSON.stringify({ nope: true }));

  expect(getRecentJobs()).toEqual([]);
});
