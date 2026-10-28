/* Куда пойти: чистая геометрия прогулок (физические пиксели экрана). */

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Цель прогулки. `feet` — на сколько ноги модели уходят ниже края окна Аи.
 *  На окно садимся, только если оно достаточно широкое и над ним есть место. */
export function pickWalkTarget(o: { work: Rect; self: Rect; fg: Rect | null; feet: number; rnd: number }): {
  x: number;
  y: number;
  kind: "floor" | "window";
} {
  const { work, self, fg, feet, rnd } = o;
  const clampX = (x: number) => Math.max(work.x, Math.min(work.x + work.w - self.w, x));
  if (fg && fg.w >= self.w * 0.6) {
    const y = fg.y - self.h + feet;
    const inside = fg.x + fg.w > work.x && fg.x < work.x + work.w;
    if (y >= work.y && inside) {
      // встаём над окном, не дальше его краёв (центр модели — над окном)
      const minX = fg.x - self.w / 2 + 40;
      const maxX = fg.x + fg.w - self.w / 2 - 40;
      const x = clampX(Math.round(minX + (maxX - minX) * rnd));
      return { x, y, kind: "window" };
    }
  }
  // «пол» — над панелью задач, в случайном месте, но не слишком близко к текущему
  const floorY = work.y + work.h - self.h + feet;
  let x = work.x + Math.round((work.w - self.w) * rnd);
  if (Math.abs(x - self.x) < self.w) {
    // слишком близко к текущему месту — идём туда, где больше простора
    const roomLeft = self.x - work.x;
    const roomRight = work.x + work.w - (self.x + self.w);
    x = self.x + (roomLeft > roomRight ? -1 : 1) * self.w * 1.5;
  }
  return { x: clampX(x), y: floorY, kind: "floor" };
}
