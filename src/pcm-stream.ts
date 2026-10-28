/* Сырой PCM 16 бит моно из ядра → кадры для WebAudio. Чистая логика без DOM. */

/** "audio/pcm;rate=24000" → { rate }. null — это не PCM (готовый файл). */
export function parsePcmType(ct: string): { rate: number } | null {
  if (!/^audio\/(pcm|l16)/i.test(ct.trim())) return null;
  const m = /rate=(\d+)/i.exec(ct);
  const rate = m ? Number(m[1]) : 24000;
  return { rate: rate >= 8000 && rate <= 96000 ? rate : 24000 };
}

/** Копит байты сети и отдаёт кадры не короче minSamples. Нечётный байт на
 *  стыке сетевых кусков переносится в следующий кадр. */
export class PcmChunker {
  private parts: Uint8Array[] = [];
  private bytes = 0;

  constructor(private minSamples: number) {}

  push(chunk: Uint8Array): Float32Array | null {
    if (chunk.length) {
      this.parts.push(chunk);
      this.bytes += chunk.length;
    }
    return this.bytes >= this.minSamples * 2 ? this.take() : null;
  }

  flush(): Float32Array | null {
    return this.bytes >= 2 ? this.take() : null;
  }

  private take(): Float32Array {
    const joined = new Uint8Array(this.bytes);
    let o = 0;
    for (const p of this.parts) {
      joined.set(p, o);
      o += p.length;
    }
    const even = this.bytes - (this.bytes % 2);
    this.parts = even < this.bytes ? [joined.slice(even)] : [];
    this.bytes -= even;
    const n = even / 2;
    const view = new DataView(joined.buffer, joined.byteOffset, even);
    const out = new Float32Array(n);
    for (let i = 0; i < n; i++) out[i] = view.getInt16(i * 2, true) / 32768;
    return out;
  }
}

/** PCM 16 бит моно → WAV (для запасного пути через decodeAudioData). */
export function pcmToWav(pcm: Uint8Array, rate: number): ArrayBuffer {
  const len = pcm.length - (pcm.length % 2);
  const out = new ArrayBuffer(44 + len);
  const v = new DataView(out);
  const w = (o: number, s: string) => {
    for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i));
  };
  w(0, "RIFF");
  v.setUint32(4, 36 + len, true);
  w(8, "WAVE");
  w(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // моно
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  w(36, "data");
  v.setUint32(40, len, true);
  new Uint8Array(out, 44).set(pcm.subarray(0, len));
  return out;
}

/** Когда ставить следующий кадр: встык, а если очередь опустела — чуть позже «сейчас». */
export function nextStart(at: number, now: number, first: boolean): number {
  if (at >= now + 0.02) return at;
  return now + (first ? 0.08 : 0.03);
}
