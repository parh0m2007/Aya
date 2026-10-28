import { describe, expect, it } from "vitest";
import { FIRST_SPEECH_CHUNK, SPEECH_CHUNK, groupSpeech, speechChunkLimit } from "./speech-group";

describe("speechChunkLimit", () => {
  it("первый кусок короче последующих", () => {
    expect(FIRST_SPEECH_CHUNK).toBe(110);
    expect(SPEECH_CHUNK).toBe(230);
    expect(speechChunkLimit(true)).toBe(110);
    expect(speechChunkLimit(false)).toBe(230);
  });
});

describe("groupSpeech", () => {
  it("первый кусок кладёт в буфер без сброса", () => {
    expect(groupSpeech("", "Привет!", 110)).toEqual({ buf: "Привет!", flush: null });
  });

  it("склеивает, пока влезает в лимит (включая ровную границу)", () => {
    expect(groupSpeech("Привет!", "Как дела?", 110)).toEqual({ buf: "Привет! Как дела?", flush: null });
    const prev = "x".repeat(100);
    expect(groupSpeech(prev, "123456789", 110)).toEqual({ buf: `${prev} 123456789`, flush: null });
  });

  it("на символе сверх лимита сбрасывает накопленное и начинает новый кусок", () => {
    const prev = "x".repeat(100);
    expect(groupSpeech(prev, "1234567890", 110)).toEqual({ buf: "1234567890", flush: prev });
  });

  it("длинная первая фраза уходит как есть, ничего не теряя", () => {
    const long = "слово ".repeat(40).trim();
    expect(groupSpeech("", long, 110)).toEqual({ buf: long, flush: null });
  });
});

import { shouldFlushEarly } from "./speech-group";

describe("shouldFlushEarly", () => {
  it("первая достаточно длинная фраза уходит сразу", () => {
    expect(shouldFlushEarly("Конечно, сейчас открою калькулятор.", false)).toBe(true);
  });
  it("коротыш ждёт продолжения, а после первой группы — обычные правила", () => {
    expect(shouldFlushEarly("Ой.", false)).toBe(false);
    expect(shouldFlushEarly("Конечно, сейчас открою калькулятор.", true)).toBe(false);
  });
});
