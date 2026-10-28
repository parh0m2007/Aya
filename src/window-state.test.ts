import { describe, expect, it, vi } from "vitest";

import {
  WINDOW_POSITION_KEY,
  parseWindowPosition,
  restoreWindowPosition,
  saveWindowPosition,
  type WindowStateApi,
} from "./window-state";

function memoryStorage(initial?: string) {
  const data = new Map<string, string>();
  if (initial !== undefined) data.set(WINDOW_POSITION_KEY, initial);
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
  };
}

function fakeApi(overrides: Partial<WindowStateApi> = {}): WindowStateApi {
  return {
    setPosition: vi.fn().mockResolvedValue(undefined),
    onMoved: vi.fn().mockResolvedValue(() => {}),
    ...overrides,
  };
}

describe("parseWindowPosition", () => {
  it("читает сохранённые координаты", () => {
    expect(parseWindowPosition('{"x":120,"y":-40}')).toEqual({ x: 120, y: -40 });
  });

  it("отбрасывает пустые и битые координаты", () => {
    for (const raw of [null, "", "null", "not json", '{"x":"a","y":2}', '{"x":1}']) {
      expect(parseWindowPosition(raw)).toBeNull();
    }
  });
});

describe("restoreWindowPosition", () => {
  it("ставит окно в сохранённую позицию", async () => {
    const api = fakeApi();
    const storage = memoryStorage('{"x":120,"y":-40}');

    await expect(restoreWindowPosition(storage, api)).resolves.toBe(true);
    expect(api.setPosition).toHaveBeenCalledWith({ x: 120, y: -40 });
  });

  it("не двигает окно без валидной позиции", async () => {
    const api = fakeApi();

    await expect(restoreWindowPosition(memoryStorage("not json"), api)).resolves.toBe(false);
    expect(api.setPosition).not.toHaveBeenCalled();
  });

  it("не падает, если Tauri не дал поставить позицию", async () => {
    const api = fakeApi({ setPosition: vi.fn().mockRejectedValue(new Error("denied")) });

    await expect(restoreWindowPosition(memoryStorage('{"x":1,"y":2}'), api)).resolves.toBe(false);
  });
});

describe("saveWindowPosition", () => {
  it("сохраняет координаты окна", () => {
    const storage = memoryStorage();

    saveWindowPosition(storage, { x: 5, y: 7 });

    expect(parseWindowPosition(storage.getItem(WINDOW_POSITION_KEY))).toEqual({ x: 5, y: 7 });
  });
});
