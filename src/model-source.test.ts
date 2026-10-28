import { describe, expect, it, vi } from "vitest";
import { loadModelConfig, modelUrlsFor } from "./model-source";

describe("modelUrlsFor", () => {
  it("ставит выбранную модель первой, затем Haru из ядра и встроенную", () => {
    const urls = modelUrlsFor(
      { live2d_model_file: "hiyori/hiyori_pro_t11.model3.json" },
      "http://core",
    );
    expect(urls).toEqual([
      "http://core/models/hiyori/hiyori_pro_t11.model3.json",
      "http://core/models/haru/Haru.model3.json",
      "models/haru/Haru.model3.json",
    ]);
  });

  it("без конфига оставляет только Haru", () => {
    expect(modelUrlsFor(null, "http://core")).toEqual([
      "http://core/models/haru/Haru.model3.json",
      "models/haru/Haru.model3.json",
    ]);
  });
});

describe("loadModelConfig", () => {
  it("повторяет запрос, пока ядро не поднимется", async () => {
    let calls = 0;
    const fakeFetch = vi.fn(async () => {
      calls += 1;
      if (calls < 3) throw new Error("core not ready");
      return {
        ok: true,
        json: async () => ({ live2d_model_file: "hiyori/hiyori_pro_t11.model3.json" }),
      } as Response;
    });

    const cfg = await loadModelConfig(fakeFetch, 1000, 1);

    expect(fakeFetch).toHaveBeenCalledTimes(3);
    expect(cfg?.live2d_model_file).toBe("hiyori/hiyori_pro_t11.model3.json");
  });

  it("возвращает null, если ядро так и не ответило", async () => {
    const fakeFetch = vi.fn(async () => {
      throw new Error("down");
    });

    const cfg = await loadModelConfig(fakeFetch, 5, 1);

    expect(cfg).toBeNull();
    expect(fakeFetch).toHaveBeenCalled();
  });
});

describe("модель с диска на старте", () => {
  it("строит адрес протокола ayamodels с кодированием частей пути", async () => {
    const { diskModelUrl } = await import("./model-source");
    expect(diskModelUrl("hiyori/hiyori_pro_t11.model3.json")).toBe("http://ayamodels.localhost/hiyori/hiyori_pro_t11.model3.json");
    expect(diskModelUrl("ми ку/a.model3.json", "http://x")).toBe("http://x/%D0%BC%D0%B8%20%D0%BA%D1%83/a.model3.json");
  });

  it("не пускает странные пути в кэш", async () => {
    const { safeModelFile } = await import("./model-source");
    expect(safeModelFile("hiyori/h.model3.json")).toBe("hiyori/h.model3.json");
    for (const bad of ["../aya.db", "a/../../b.model3.json", "C:/x/a.model3.json", "a\\b.model3.json", "a/b.json", "", null, 5]) {
      expect(safeModelFile(bad)).toBeNull();
    }
  });

  it("запоминает и забывает модель", async () => {
    const { cachedModelFile, rememberModelFile } = await import("./model-source");
    const m = new Map<string, string>();
    const store = { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) };
    rememberModelFile("miara/miara.model3.json", store);
    expect(cachedModelFile(store)).toBe("miara/miara.model3.json");
    rememberModelFile("", store);
    expect(cachedModelFile(store)).toBeNull();
    expect(cachedModelFile({ getItem: () => { throw new Error("blocked"); } })).toBeNull();
  });
});
