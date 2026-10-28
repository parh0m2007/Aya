let permission: Promise<boolean> | null = null;

async function ensurePermission(): Promise<boolean> {
  if (!permission) {
    permission = (async () => {
      try {
        const n = await import("@tauri-apps/plugin-notification");
        if (await n.isPermissionGranted()) return true;
        return (await n.requestPermission()) === "granted";
      } catch {
        return false;
      }
    })();
  }
  return permission;
}

export async function notifyAya(body: string): Promise<boolean> {
  if (!body.trim()) return false;
  if (!(await ensurePermission())) return false;
  try {
    const n = await import("@tauri-apps/plugin-notification");
    n.sendNotification({ title: "Ая", body });
    return true;
  } catch {
    return false;
  }
}
