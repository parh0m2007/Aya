/** Инициатива Аи: что она говорит сама и что человек попросил сам. */

/** Напоминание или отчёт фонового суб-агента — об этом человек просил сам. */
export function isRequested(topic: string): boolean {
  return topic.startsWith("reminder:") || topic.startsWith("report:");
}

/** «Ая сама заговаривает» выключено — ядро отдаёт только напоминания и отчёты. */
export function initiativeQuery(initiativeOn: boolean): string {
  return initiativeOn ? "" : "?requested_only=1";
}

export type Delivery = "full" | "quiet" | "drop";

/** Что делать с готовой репликой. interrupted — пока она готовилась, начался
 *  новый ход или запись. Ядро уже сняло напоминание с очереди, поэтому его не
 *  выбрасываем: тихо пишем в чат и уведомлением. */
export function initiativeDelivery(topic: string, initiativeOn: boolean, interrupted: boolean): Delivery {
  if (isRequested(topic)) return interrupted ? "quiet" : "full";
  if (!initiativeOn || interrupted) return "drop";
  return "full";
}
