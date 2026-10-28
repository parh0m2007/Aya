export interface PendingAction {
  id: string;
  tool: string;
  args: Record<string, unknown>;
}

export interface ChatResponse {
  reply: string;
  mood: string;
  activity: string;
  pending?: PendingAction | null;
  speechPlan?: string;
  /** Состояние собственного плеера Аи после хода (если модель включила музыку). */
  music?: unknown;
}

export interface SpeechPiece {
  id: string;
  seq: number;
  plan: string;
  /** Облачный голос: кусок приходит потоком, играть можно с первых байт. */
  stream?: boolean;
}

export interface ChatOptions {
  /** Ядро само озвучивает ответ по ходу генерации и шлёт события speech. */
  speak?: boolean;
  /** Пути файлов, брошенных на аватар. */
  attachments?: string[];
  onSpeech?: (p: SpeechPiece) => void;
  onFiles?: (files: { name: string; kind: string; error: string }[]) => void;
}

export async function connectCore(
  coreUrl: string,
  text: string,
  onDelta?: (chunk: string) => void,
  opts: ChatOptions = {},
): Promise<ChatResponse> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 120000);
  let res: Response;
  try {
    res = await fetch(`${coreUrl}/api/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, speak: !!opts.speak, attachments: opts.attachments ?? [] }),
      signal: ctl.signal,
    });
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok || !res.body) throw new Error(await res.text().catch(() => `HTTP ${res.status}`));

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let final: ChatResponse | null = null;

  const processLine = (line: string) => {
    if (!line.trim()) return;
    let msg: any;
    try {
      msg = JSON.parse(line);
    } catch {
      return;
    }
    if (msg.error && !msg.done) throw new Error(msg.error);
    if (msg.files) opts.onFiles?.(msg.files);
    if (msg.delta) onDelta?.(msg.delta);
    if (msg.speech) opts.onSpeech?.(msg.speech as SpeechPiece);
    if (msg.done) {
      final = { reply: msg.reply, mood: msg.mood, activity: msg.activity, speechPlan: msg.speech_plan, music: msg.music };
      if (msg.pending) final.pending = msg.pending;
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, idx);
      buf = buf.slice(idx + 1);
      processLine(line);
    }
  }
  if (buf.trim()) processLine(buf);
  if (!final) throw new Error("стрим оборвался без ответа");
  return final;
}

export function speechPieceUrl(coreUrl: string, id: string): string {
  return `${coreUrl}/api/speech/${encodeURIComponent(id)}`;
}

export function cancelSpeechPlan(coreUrl: string, plan: string): void {
  void fetch(`${coreUrl}/api/speech/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan }),
  }).catch(() => {});
}

export async function transcribe(coreUrl: string, blob: Blob): Promise<string> {
  const form = new FormData();
  form.append("file", blob, "voice.webm");
  const res = await fetch(`${coreUrl}/api/voice/transcribe`, { method: "POST", body: form });
  if (!res.ok) throw new Error("stt failed");
  const data = await res.json();
  return data.text || "";
}

export function speakUrl(coreUrl: string, text: string, mood?: string): string {
  const m = mood ? `&mood=${encodeURIComponent(mood)}` : "";
  return `${coreUrl}/api/speak?text=${encodeURIComponent(text)}${m}`;
}
