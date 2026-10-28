/** Адрес ядра. Для отладки можно переопределить: localStorage["aya.coreUrl"]. */
export const CORE_URL = (() => {
  try {
    return localStorage.getItem("aya.coreUrl") || "http://127.0.0.1:8330";
  } catch {
    return "http://127.0.0.1:8330";
  }
})();

const RU: Record<string, string> = {
  calm: "спокойная",
  joyful: "радостная",
  thoughtful: "задумчивая",
  sad: "грустит",
  surprise: "удивлена",
  rest: "отдыхает",
  talk: "разговор",
  read: "читает",
  sketch: "рисует",
  idle: "ждёт",
  task: "занята делом",
};

/** Строка состояния под именем: «радостная · читает». Имя показывается отдельно,
 *  поэтому второй аргумент нужен только для пустого состояния. */
export function stateLabel(s: { mood?: string; activity?: string }, name = ""): string {
  const parts: string[] = [];
  if (s.mood && RU[s.mood]) parts.push(RU[s.mood]);
  if (s.activity && RU[s.activity]) parts.push(RU[s.activity]);
  if (parts.length) return parts.join(" · ");
  return name ? "рядом" : "Ая";
}
