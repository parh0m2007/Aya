/** Что показать в карточке «разрешить?» перед действием Аи. */

// То, что человек одобряет, видно целиком: команда, сообщение, адресат.
const FULL = new Set(["command", "text", "chat", "host", "name", "target", "url"]);
const HEAD = 600;

export function previewArgs(args: Record<string, unknown>): string {
  const lines = Object.entries(args).map(([k, v]) => {
    const s = String(v);
    if (FULL.has(k) || s.length <= HEAD) return `${k}: ${s}`;
    return `${k}: ${s.slice(0, HEAD)}… (ещё ${s.length - HEAD} символов)`;
  });
  return lines.join("\n") || "(без аргументов)";
}
