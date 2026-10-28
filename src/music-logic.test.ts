import { describe, expect, it } from "vitest";
import { musicStep, nowPlayingLine, rampVolume, type MusicSnap } from "./music-logic";

const snap = (over: Partial<MusicSnap>): MusicSnap => ({
  seq: 2,
  cmd: "load",
  status: "playing",
  pos: 0,
  count: 3,
  current: { id: "1:2", title: "Группа крови", artist: "КИНО" },
  ...over,
});

describe("musicStep", () => {
  const idle = { seq: 1, itemId: "", playing: false };
  it("starts a new track", () => {
    expect(musicStep(idle, snap({}))).toBe("start");
  });
  it("ignores the same seq", () => {
    expect(musicStep({ seq: 2, itemId: "", playing: false }, snap({}))).toBe("none");
  });
  it("pauses and resumes the same track", () => {
    const on = { seq: 2, itemId: "1:2", playing: true };
    expect(musicStep(on, snap({ seq: 3, status: "paused" }))).toBe("pause");
    expect(musicStep({ ...on, playing: false }, snap({ seq: 4, status: "playing" }))).toBe("resume");
  });
  it("stops when the queue ends", () => {
    expect(musicStep({ seq: 2, itemId: "1:2", playing: true }, snap({ seq: 3, status: "idle", current: null }))).toBe("stop");
    expect(musicStep(idle, snap({ seq: 3, status: "idle", current: null }))).toBe("none");
  });
  it("switches to the next track", () => {
    const next = snap({ seq: 3, cmd: "track", current: { id: "9", title: "x", artist: "y" } });
    expect(musicStep({ seq: 2, itemId: "1:2", playing: true }, next)).toBe("start");
  });
  it("tolerates garbage", () => {
    expect(musicStep(idle, null)).toBe("none");
    expect(musicStep(idle, {} as MusicSnap)).toBe("none");
  });
});

describe("helpers", () => {
  it("ramps volume smoothly", () => {
    expect(rampVolume(1, 0.25)).toBeCloseTo(0.92);
    expect(rampVolume(0.3, 0.25)).toBe(0.25);
  });
  it("formats now playing", () => {
    expect(nowPlayingLine({ id: "1", title: "Утро", artist: "Дайте танк (!)" })).toBe("♪ Дайте танк (!) — Утро");
    expect(nowPlayingLine(null)).toBe("");
  });
});
