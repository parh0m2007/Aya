import { describe, expect, it } from "vitest";
import { previewArgs } from "./action-preview";

describe("previewArgs", () => {
  it("команду показывает целиком — хвост длинной команды раньше обрезался на 300 символах", () => {
    const command = "Get-ChildItem; ".repeat(40) + "Remove-Item -Recurse C:\\важное";
    const out = previewArgs({ command });
    expect(out).toContain("Remove-Item -Recurse C:\\важное");
  });
  it("текст сообщения в Telegram — целиком", () => {
    const text = "привет ".repeat(80) + "конец";
    expect(previewArgs({ chat: "мама", text })).toContain("конец");
  });
  it("длинное содержимое файла — начало и сколько ещё", () => {
    const out = previewArgs({ name: "a.md", content: "я".repeat(5000) });
    expect(out).toContain("name: a.md");
    expect(out).toContain("ещё 4400 символов");
    expect(out.length).toBeLessThan(700);
  });
  it("без аргументов", () => {
    expect(previewArgs({})).toBe("(без аргументов)");
  });
});
