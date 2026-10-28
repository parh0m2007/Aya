/* Обновления в один клик: проверка GitHub Releases, полоска «Доступна версия… — обновить?». */
import { updateLine } from "./updater-logic";

type Offer = { line: string; install: () => Promise<void> };

/** null — обновлений нет, нет сети или обновления не настроены (нет ключа подписи). */
export async function checkForUpdate(): Promise<Offer | null> {
  try {
    const { check } = await import("@tauri-apps/plugin-updater");
    const { getVersion } = await import("@tauri-apps/api/app");
    const upd = await check();
    if (!upd) return null;
    const line = updateLine(await getVersion(), upd.version);
    if (!line) return null;
    return {
      line,
      install: async () => {
        await upd.downloadAndInstall();
        const { relaunch } = await import("@tauri-apps/plugin-process");
        await relaunch();
      },
    };
  } catch {
    return null; // сеть, нет плагина — молча
  }
}

function banner(offer: Offer) {
  if (document.querySelector(".update-bar")) return;
  const bar = document.createElement("div");
  bar.className = "update-bar";
  const text = document.createElement("span");
  text.textContent = offer.line;
  const go = document.createElement("button");
  go.textContent = "Обновить";
  const later = document.createElement("button");
  later.className = "later";
  later.textContent = "Потом";
  go.addEventListener("click", async () => {
    go.disabled = later.disabled = true;
    text.textContent = "Скачиваю обновление… Ая перезапустится сама";
    try {
      await offer.install();
    } catch {
      text.textContent = "Не получилось обновиться — попробую позже";
      window.setTimeout(() => bar.remove(), 5000);
    }
  });
  later.addEventListener("click", () => bar.remove());
  bar.append(text, go, later);
  document.body.append(bar);
}

/** Через 30 с после старта и потом раз в 6 часов. */
export function startUpdateChecks(onOffer: (line: string) => void = () => {}) {
  const run = async () => {
    const offer = await checkForUpdate();
    if (!offer) return;
    onOffer(offer.line);
    banner(offer);
  };
  window.setTimeout(() => void run(), 30_000);
  window.setInterval(() => void run(), 6 * 3600_000);
}
