import { describe, expect, it } from "vitest";
import { buildSavePayload, buildServerPayload, formatArgs, formatEnv, parseArgs, parseEnv, validateServerName } from "./mcp-editor";

describe("parseArgs", () => {
  it("по одному аргументу в строке, пустые пропускает", () => {
    expect(parseArgs("npx\n  -y \n\n@playwright/mcp@latest")).toEqual(["npx", "-y", "@playwright/mcp@latest"]);
  });
});

describe("parseEnv", () => {
  it("KEY=VALUE, значение может содержать =", () => {
    expect(parseEnv("A=1\n\nB=x=y\n C = 2 ")).toEqual({
      env: { A: "1", B: "x=y", C: "2" },
      errors: [],
    });
  });
  it("битые строки попадают в errors", () => {
    const r = parseEnv("BAD\n=x\nOK=1");
    expect(r.env).toEqual({ OK: "1" });
    expect(r.errors).toHaveLength(2);
  });
});

describe("format", () => {
  it("roundtrip", () => {
    expect(parseArgs(formatArgs(["a", "b"]))).toEqual(["a", "b"]);
    expect(parseEnv(formatEnv({ A: "1", B: "x=y" }))).toEqual({ env: { A: "1", B: "x=y" }, errors: [] });
  });
});

describe("validateServerName", () => {
  it("разрешает латиницу, цифры и дефис", () => {
    expect(validateServerName("playwright")).toBeNull();
    expect(validateServerName("my-server-2")).toBeNull();
  });
  it("отклоняет пустое, пробелы, uppercase и длинное", () => {
    expect(validateServerName("")).not.toBeNull();
    expect(validateServerName("Bad")).not.toBeNull();
    expect(validateServerName("a b")).not.toBeNull();
    expect(validateServerName("a".repeat(49))).not.toBeNull();
  });
});

describe("build payload", () => {
  const form = {
    name: "echo",
    command: "python",
    argsText: "-u\nsrv.py",
    envText: "K=V",
    cwd: "C:\\tmp",
    enabled: true,
  };
  it("собирает сервер и save-полезную нагрузку", () => {
    expect(buildServerPayload(form)).toEqual({
      name: "echo",
      command: "python",
      args: ["-u", "srv.py"],
      env: { K: "V" },
      cwd: "C:\\tmp",
      enabled: true,
    });
    expect(buildSavePayload(form, "old").old_name).toBe("old");
    expect(buildSavePayload(form, " old ").old_name).toBe("old");
  });
  it("бросает понятные ошибки", () => {
    expect(() => buildServerPayload({ ...form, envText: "BAD" })).toThrow(/KEY=VALUE/);
    expect(() => buildServerPayload({ ...form, command: "  " })).toThrow(/команда/);
    expect(() => buildServerPayload({ ...form, name: "Bad" })).toThrow(/имя/);
  });
});
