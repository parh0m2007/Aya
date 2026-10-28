import { beforeEach, describe, expect, it, vi } from "vitest";

const { startDragging } = vi.hoisted(() => ({ startDragging: vi.fn() }));

vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: () => ({ startDragging }),
}));

import { startWindowDrag } from "./window-drag";

describe("startWindowDrag", () => {
  beforeEach(() => {
    startDragging.mockReset();
  });

  it("запускает нативное перетаскивание окна", async () => {
    startDragging.mockResolvedValue(undefined);

    await expect(startWindowDrag()).resolves.toBe(true);
    expect(startDragging).toHaveBeenCalledTimes(1);
  });

  it("не падает вне Tauri", async () => {
    startDragging.mockRejectedValue(new Error("not tauri"));

    await expect(startWindowDrag()).resolves.toBe(false);
  });
});
