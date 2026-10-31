/* Кнопки и переключатели консоли: сбой показываем человеку, а не глотаем молча
   (раньше «Сохранить» при недоступном ядре просто ничего не делало). */

export function failText(e: unknown): string {
  // fetch без ответа ядра падает с TypeError («Failed to fetch»)
  if (e instanceof TypeError) return "ядро не отвечает";
  return `не получилось: ${e instanceof Error ? e.message : String(e)}`;
}

export async function runGuarded(work: () => Promise<void>, onFail: (message: string) => void): Promise<boolean> {
  try {
    await work();
    return true;
  } catch (e) {
    onFail(failText(e));
    return false;
  }
}
