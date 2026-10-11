/** Client-side download of generated text (TXT/SRT/VTT). */
export function downloadText(
  filename: string,
  text: string,
  mime = "text/plain",
): void {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
