export type TrayCheck = "followup" | "focus";

export async function setTrayCheck(id: TrayCheck, checked: boolean): Promise<boolean> {
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("set_tray_check", { id, checked });
    return true;
  } catch {
    return false;
  }
}

export async function onTrayToggle(id: TrayCheck, handler: () => void | Promise<void>): Promise<void> {
  try {
    const { listen } = await import("@tauri-apps/api/event");
    await listen(`tray-toggle-${id}`, () => void handler());
  } catch {}
}
