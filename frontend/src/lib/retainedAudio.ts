/**
 * In-memory association between a job and the audio File the user uploaded.
 *
 * Deliberately NOT persisted: the browser tab is the lifetime of playback.
 * After a reload the transcript still renders; playback is honestly marked
 * unavailable (the API serves no audio stream yet).
 */

const files = new Map<string, File>();

export function setRetainedAudio(jobId: string, file: File): void {
  files.set(jobId, file);
}

export function getRetainedAudio(jobId: string): File | undefined {
  return files.get(jobId);
}

export function clearRetainedAudio(): void {
  files.clear();
}
