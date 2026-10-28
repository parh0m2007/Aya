/* Обновления: сравнение версий и текст предложения. */

function parts(v: string): number[] {
  return v.replace(/^v/i, "").split(/[.+-]/).slice(0, 3).map((x) => Number.parseInt(x, 10) || 0);
}

/** a новее b (по числам: 1.0.10 > 1.0.9). */
export function newer(a: string, b: string): boolean {
  const [x, y] = [parts(a), parts(b)];
  for (let i = 0; i < 3; i++) if ((x[i] ?? 0) !== (y[i] ?? 0)) return (x[i] ?? 0) > (y[i] ?? 0);
  return false;
}

export function updateLine(current: string, next: string): string | null {
  return newer(next, current) ? `Доступна версия ${next.replace(/^v/i, "")} — обновить?` : null;
}
