export function shouldToast(hidden: boolean, focus: boolean): boolean {
  return hidden || focus;
}

export function toastBody(text: string, limit = 240): string {
  const clean = text.replace(/\s+/g, " ").trim();
  if (clean.length <= limit) return clean;
  return clean.slice(0, Math.max(0, limit - 1)).trimEnd() + "…";
}
