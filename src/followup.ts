export const FOLLOWUP_MIN = 2;
export const FOLLOWUP_MAX = 30;
export const FOLLOWUP_DEFAULT = 6;

export type VoiceMode = "button" | "auto" | "hotkey";

export function clampFollowupSec(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return FOLLOWUP_DEFAULT;
  return Math.max(FOLLOWUP_MIN, Math.min(FOLLOWUP_MAX, Math.round(n)));
}

export class FollowUpWindow {
  private deadline = 0;

  arm(seconds: number, now = Date.now()): void {
    this.deadline = now + clampFollowupSec(seconds) * 1000;
  }

  active(now = Date.now()): boolean {
    return this.deadline > 0 && now < this.deadline;
  }

  remaining(now = Date.now()): number {
    if (this.deadline <= 0) return 0;
    return Math.max(0, Math.ceil((this.deadline - now) / 1000));
  }

  expire(now = Date.now()): boolean {
    if (this.deadline > 0 && now >= this.deadline) {
      this.deadline = 0;
      return true;
    }
    return false;
  }

  cancel(): void {
    this.deadline = 0;
  }
}

export interface ArmDecision {
  enabled: boolean;
  voiceTurn: boolean;
  mode: VoiceMode;
  panelOpen: boolean;
  hidden: boolean;
  busy: boolean;
  pending: boolean;
  reply: string;
  spoke: boolean;
  aborted: boolean;
}

export function shouldArmFollowUp(d: ArmDecision): boolean {
  return (
    d.enabled &&
    d.voiceTurn &&
    d.mode !== "auto" &&
    d.panelOpen &&
    !d.hidden &&
    !d.busy &&
    !d.pending &&
    !d.aborted &&
    d.spoke &&
    d.reply.trim().length > 0
  );
}

/** После ответа на «Ая, …» можно ещё 7 секунд говорить без имени — но только
 *  при включённых «свободных руках»: выключил их — после ответа Ая не слушает. */
export function shouldArmWakeWindow(d: { wakeOn: boolean; followupOn: boolean; mode: VoiceMode }): boolean {
  return d.wakeOn && d.followupOn && d.mode !== "auto";
}
