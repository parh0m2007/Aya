export interface McpForm {
  name: string;
  command: string;
  argsText: string;
  envText: string;
  cwd: string;
  enabled: boolean;
}

export interface McpServerPayload {
  name: string;
  command: string;
  args: string[];
  env: Record<string, string>;
  cwd: string;
  enabled: boolean;
}

export interface McpSavePayload extends McpServerPayload {
  old_name: string;
}

export function parseArgs(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

export function parseEnv(text: string): { env: Record<string, string>; errors: string[] } {
  const env: Record<string, string> = {};
  const errors: string[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const eq = line.indexOf("=");
    if (eq < 0) {
      errors.push(`строка «${line}»: нужен формат KEY=VALUE`);
      continue;
    }
    const key = line.slice(0, eq).trim();
    const value = line.slice(eq + 1).trim();
    if (!key) {
      errors.push(`строка «${line}»: пустой ключ, нужен формат KEY=VALUE`);
      continue;
    }
    env[key] = value;
  }
  return { env, errors };
}

export function formatArgs(args: string[]): string {
  return args.join("\n");
}

export function formatEnv(env: Record<string, string>): string {
  return Object.entries(env)
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
}

export function validateServerName(name: string): string | null {
  if (!/^[a-z0-9][a-z0-9-]{0,47}$/.test(name.trim())) {
    return "имя: 1–48 символов, латиница в нижнем регистре, цифры и дефис";
  }
  return null;
}

export function buildServerPayload(form: McpForm): McpServerPayload {
  const name = form.name.trim();
  const nameError = validateServerName(name);
  if (nameError) throw new Error(nameError);
  const command = form.command.trim();
  if (!command) throw new Error("команда не может быть пустой");
  const { env, errors } = parseEnv(form.envText);
  if (errors.length > 0) throw new Error(errors.join("\n"));
  return {
    name,
    command,
    args: parseArgs(form.argsText),
    env,
    cwd: form.cwd.trim(),
    enabled: form.enabled,
  };
}

export function buildSavePayload(form: McpForm, oldName = ""): McpSavePayload {
  return { ...buildServerPayload(form), old_name: oldName.trim() };
}
