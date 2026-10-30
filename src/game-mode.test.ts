import { describe, expect, it } from "vitest";
import { gameVoiceMode, listensAlways, rendersNow } from "./game-mode";

describe("игровой режим в окне", () => {
  it("в игре говорить можно только по клавише, в любом режиме голоса", () => {
    expect(gameVoiceMode("button", true)).toBe("hotkey");
    expect(gameVoiceMode("auto", true)).toBe("hotkey");
    expect(gameVoiceMode("button", false)).toBe("button");
    expect(gameVoiceMode("auto", false)).toBe("auto");
  });

  it("в игре микрофон не слушает постоянно — ни «по голосу», ни «Ая, …»", () => {
    expect(listensAlways("auto", false, true)).toBe(false);
    expect(listensAlways("button", true, true)).toBe(false);
    expect(listensAlways("auto", false, false)).toBe(true);
    expect(listensAlways("button", true, false)).toBe(true);
    expect(listensAlways("hotkey", false, false)).toBe(false);
  });

  it("в игре аватар не рисуется, даже когда окно формально видно", () => {
    expect(rendersNow(true, false)).toBe(false);
    expect(rendersNow(false, false)).toBe(true);
    expect(rendersNow(false, true)).toBe(false);
  });
});
