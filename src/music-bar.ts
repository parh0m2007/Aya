/* Плеер в окне Аи — для Яндекс Музыки, которая играет в самой Ае:
 * пауза/продолжить, предыдущий/следующий трек, тише/громче, выключить.
 * Показываем, пока свой плеер играет или стоит на паузе. */
import { barView, type BarView, type MusicSnap } from "./music-logic";

const FRESH_MS = 4000; // новый трек — плеер на пару секунд ярче

export interface MusicBar {
  render(snap: MusicSnap | null): void;
}

export function initMusicBar(root: HTMLElement, send: (cmd: string) => Promise<boolean>): MusicBar {
  const q = <T extends Element>(sel: string) => root.querySelector(sel) as T;
  const title = q<HTMLElement>("[data-mb-title]");
  const artist = q<HTMLElement>("[data-mb-artist]");
  const vol = q<HTMLOutputElement>("[data-mb-vol]");
  const play = q<HTMLButtonElement>('[data-mb="toggle"]');
  const playIcon = play.querySelector("use")!;
  const louder = q<HTMLButtonElement>('[data-mb="volup"]');
  const quieter = q<HTMLButtonElement>('[data-mb="voldown"]');

  let view: BarView = barView(null);
  let trackKey = "";
  let freshTimer = 0;

  const render = (snap: MusicSnap | null) => {
    view = barView(snap);
    root.classList.toggle("hidden", !view.visible);
    if (!view.visible) {
      trackKey = "";
      return;
    }
    const key = `${snap?.current?.id ?? ""}`;
    if (key !== trackKey) {
      trackKey = key;
      root.classList.add("fresh");
      window.clearTimeout(freshTimer);
      freshTimer = window.setTimeout(() => root.classList.remove("fresh"), FRESH_MS);
    }
    title.textContent = view.title || "Без названия";
    artist.textContent = view.artist;
    root.title = [view.artist, view.title].filter(Boolean).join(" — ");
    playIcon.setAttribute("href", view.playing ? "#i-pause" : "#i-play");
    const playLabel = view.playing ? "Пауза" : "Продолжить";
    play.setAttribute("aria-label", playLabel);
    play.title = playLabel;
    vol.textContent = `${view.volume}%`;
    louder.disabled = !view.canLouder;
    quieter.disabled = !view.canQuieter;
  };

  root.addEventListener("click", async (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-mb]");
    if (!btn || btn.disabled || btn.classList.contains("busy")) return;
    const act = btn.dataset.mb!;
    const cmd = act === "toggle" ? (view.playing ? "pause" : "play") : act;
    btn.classList.add("busy");
    const ok = await send(cmd);
    btn.classList.remove("busy");
    if (!ok) {
      root.classList.add("error");
      root.title = "Ядро не отвечает — попробуй ещё раз";
      window.setTimeout(() => root.classList.remove("error"), 1500);
    }
  });

  return { render };
}
