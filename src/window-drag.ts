export async function startWindowDrag(): Promise<boolean> {
  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    await getCurrentWindow().startDragging();
    return true;
  } catch {
    return false;
  }
}
