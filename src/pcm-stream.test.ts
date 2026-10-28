import { describe, expect, it } from "vitest";
import { PcmChunker, nextStart, parsePcmType, pcmToWav } from "./pcm-stream";

const le = (...vals: number[]) => {
  const b = new Uint8Array(vals.length * 2);
  const v = new DataView(b.buffer);
  vals.forEach((x, i) => v.setInt16(i * 2, x, true));
  return b;
};

describe("parsePcmType", () => {
  it("reads rate and rejects files", () => {
    expect(parsePcmType("audio/pcm;rate=24000")).toEqual({ rate: 24000 });
    expect(parsePcmType("audio/L16; rate=16000")).toEqual({ rate: 16000 });
    expect(parsePcmType("audio/pcm")).toEqual({ rate: 24000 });
    expect(parsePcmType("audio/wav")).toBeNull();
    expect(parsePcmType("audio/mpeg")).toBeNull();
  });
});

describe("PcmChunker", () => {
  it("waits for enough samples, then converts", () => {
    const c = new PcmChunker(3);
    expect(c.push(le(16384, -16384))).toBeNull();
    const f = c.push(le(0, 32767));
    expect(f && Array.from(f)).toEqual([0.5, -0.5, 0, 32767 / 32768]);
  });

  it("carries an odd byte across network chunks", () => {
    const c = new PcmChunker(1);
    const all = le(1000, -2000, 3000);
    const a = c.push(all.slice(0, 3)); // 1 сэмпл + половинка второго
    const b = c.push(all.slice(3));
    expect(a && a.length).toBe(1);
    expect(b && Array.from(b).map((x) => Math.round(x * 32768))).toEqual([-2000, 3000]);
    expect(c.flush()).toBeNull();
  });

  it("flush returns the tail", () => {
    const c = new PcmChunker(100);
    c.push(le(5, 6));
    expect(c.flush()?.length).toBe(2);
  });
});

describe("pcmToWav", () => {
  it("writes a valid header", () => {
    const w = new DataView(pcmToWav(le(1, 2, 3), 24000));
    expect(String.fromCharCode(w.getUint8(0), w.getUint8(1), w.getUint8(2), w.getUint8(3))).toBe("RIFF");
    expect(w.getUint32(24, true)).toBe(24000);
    expect(w.getUint32(40, true)).toBe(6);
    expect(w.byteLength).toBe(50);
  });
});

describe("nextStart", () => {
  it("plays back to back, re-anchors after underrun", () => {
    expect(nextStart(5, 4, false)).toBe(5);
    expect(nextStart(0, 4, true)).toBeCloseTo(4.08);
    expect(nextStart(3.99, 4, false)).toBeCloseTo(4.03);
  });
});
