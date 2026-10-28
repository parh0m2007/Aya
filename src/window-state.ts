export const WINDOW_POSITION_KEY = "aya.window.position.v1";

export interface WindowPosition {
  x: number;
  y: number;
}

export interface WindowStateApi {
  setPosition(position: WindowPosition): Promise<void>;
  onMoved(handler: (position: WindowPosition) => void): Promise<() => void>;
}

export function parseWindowPosition(raw: string | null): WindowPosition | null {
  if (!raw) return null;
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!value || typeof value !== "object") return null;
  const { x, y } = value as { x?: unknown; y?: unknown };
  if (
    typeof x !== "number" ||
    typeof y !== "number" ||
    !Number.isFinite(x) ||
    !Number.isFinite(y)
  ) {
    return null;
  }
  return { x, y };
}

export function saveWindowPosition(
  storage: Pick<Storage, "setItem">,
  position: WindowPosition,
): void {
  try {
    storage.setItem(WINDOW_POSITION_KEY, JSON.stringify({ x: position.x, y: position.y }));
  } catch {
    // localStorage can be unavailable; the position is best-effort.
  }
}

export async function restoreWindowPosition(
  storage: Pick<Storage, "getItem">,
  api: WindowStateApi,
): Promise<boolean> {
  const position = parseWindowPosition(storage.getItem(WINDOW_POSITION_KEY));
  if (!position) return false;
  try {
    await api.setPosition(position);
    return true;
  } catch {
    return false;
  }
}

export async function initWindowState(): Promise<void> {
  try {
    const { getCurrentWindow, PhysicalPosition } = await import("@tauri-apps/api/window");
    const win = getCurrentWindow();
    const api: WindowStateApi = {
      setPosition: (position) => win.setPosition(new PhysicalPosition(position.x, position.y)),
      onMoved: (handler) =>
        win.onMoved(({ payload }) => handler({ x: payload.x, y: payload.y })),
    };
    await restoreWindowPosition(localStorage, api);
    await api.onMoved((position) => saveWindowPosition(localStorage, position));
  } catch {
    // Outside Tauri (or without localStorage), window state is simply not persisted.
  }
}

export async function isAyaHidden(): Promise<boolean> {
  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    const w = getCurrentWindow();
    return !(await w.isVisible()) || (await w.isMinimized());
  } catch {
    return typeof document !== "undefined" && document.visibilityState === "hidden";
  }
}
