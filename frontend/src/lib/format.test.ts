import {
  formatBytes,
  formatClock,
  formatDuration,
  formatTimestamp,
} from "./format";

test("formatDuration renders m:ss.s for short durations", () => {
  expect(formatDuration(65.5)).toBe("1:05.5");
  expect(formatDuration(9.25)).toBe("0:09.3");
});

test("formatDuration renders h:mm:ss.s past an hour", () => {
  expect(formatDuration(3661.5)).toBe("1:01:01.5");
});

test("formatTimestamp renders h:mm:ss.mmm with millisecond precision", () => {
  expect(formatTimestamp(65.5)).toBe("1:05.500");
  expect(formatTimestamp(3661.234)).toBe("1:01:01.234");
  expect(formatTimestamp(0)).toBe("0:00.000");
});

test("formatBytes scales units", () => {
  expect(formatBytes(900)).toBe("900 B");
  expect(formatBytes(1536)).toBe("1.5 KB");
  expect(formatBytes(2 * 1024 * 1024)).toBe("2.0 MB");
});

describe("formatClock", () => {
  test("reads as a recorder timer", () => {
    expect(formatClock(0)).toBe("0:00");
    expect(formatClock(7)).toBe("0:07");
    expect(formatClock(65)).toBe("1:05");
    expect(formatClock(3600)).toBe("1:00:00");
  });
});
