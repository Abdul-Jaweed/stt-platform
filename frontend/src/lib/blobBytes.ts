/**
 * The bytes of a Blob.
 *
 * `Blob.arrayBuffer()` is missing in the jsdom the tests run in (and jsdom's
 * Blob has no `.text()` either), so the round trip goes through FileReader —
 * the same choice `src/lib/download.ts` makes for downloads.
 *
 * Note for tests: install this with REAL timers. With `vi.useFakeTimers()`
 * FileReader's callbacks never fire and the promise never settles.
 */
export function blobBytes(blob: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () =>
      reject(reader.error ?? new Error("the recording could not be read"));
    reader.readAsArrayBuffer(blob);
  });
}
