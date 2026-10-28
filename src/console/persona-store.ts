/* Профиль персоны для конструктора: типы, подписи и чистые помощники. */

export type Trait = "warmth" | "humor" | "directness" | "curiosity" | "energy";
export type ModuleId = "voice" | "initiative" | "memory" | "actions" | "avatar_reactions" | "idle_life";

export interface Profile {
  name: string;
  user_name: string;
  role: string;
  role_text: string;
  traits: Record<Trait, number>;
  length: "short" | "medium" | "long";
  address: "ты" | "вы";
  emoji: boolean;
  tags: string[];
  interests: string;
  about: string;
  greeting: string;
  modules: Record<ModuleId, boolean>;
  configured: boolean;
}

export interface Preset {
  id: string;
  title: string;
  sub: string;
  patch: Partial<Omit<Profile, "traits" | "modules">> & {
    traits?: Partial<Record<Trait, number>>;
    modules?: Partial<Record<ModuleId, boolean>>;
  };
}

export interface PersonaView {
  profile: Profile;
  roles: { id: string; title: string; sub: string }[];
  presets: Preset[];
}

export const TRAITS: { id: Trait; title: string; lo: string; hi: string }[] = [
  { id: "warmth", title: "Теплота", lo: "сдержанная", hi: "заботливая" },
  { id: "humor", title: "Юмор", lo: "серьёзная", hi: "шутница" },
  { id: "directness", title: "Прямота", lo: "деликатная", hi: "прямолинейная" },
  { id: "curiosity", title: "Любопытство", lo: "не расспрашивает", hi: "любопытная" },
  { id: "energy", title: "Энергия", lo: "тихая", hi: "энергичная" },
];

export const MODULES: { id: ModuleId; title: string; sub: string; icon: string }[] = [
  { id: "voice", title: "Голос", sub: "озвучивает ответы; без него — только текст", icon: "i-wave" },
  { id: "initiative", title: "Инициатива", sub: "сама заговаривает: утро, перерывы, напоминания", icon: "i-spark" },
  { id: "memory", title: "Долгая память", sub: "помнит прошлые разговоры и находит детали", icon: "i-book" },
  { id: "actions", title: "Действия на ПК", sub: "открыть приложение, громкость, заметки, поиск в сети", icon: "i-bolt" },
  { id: "avatar_reactions", title: "Реакции на касания", sub: "отвечает, когда гладишь или тыкаешь", icon: "i-heart" },
  { id: "idle_life", title: "Жизнь в простое", sub: "оглядывается, двигается, дремлет без тебя", icon: "i-moon" },
];

export const LENGTHS: { id: Profile["length"]; title: string }[] = [
  { id: "short", title: "Коротко" },
  { id: "medium", title: "Средне" },
  { id: "long", title: "Развёрнуто" },
];

export const TAG_IDEAS = [
  "ироничная",
  "заботливая",
  "любит тишину",
  "обожает аниме",
  "геймерша",
  "любит котов",
  "кофеманка",
  "пунктуальная",
  "мечтательная",
  "немного вредная",
];

export function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T;
}

/** Наложить пресет на черновик: черты и модули — поверх, остальное заменяется. */
export function applyPreset(draft: Profile, preset: Preset): Profile {
  const out = clone(draft);
  const { traits, modules, ...rest } = preset.patch;
  Object.assign(out, clone(rest));
  if (traits) out.traits = { ...out.traits, ...traits };
  if (modules) out.modules = { ...out.modules, ...modules };
  return out;
}

/** Добавить тег: без дублей и пустых, не больше 8. */
export function addTag(tags: string[], tag: string): string[] {
  const t = tag.trim().slice(0, 40);
  if (!t || tags.some((x) => x.toLowerCase() === t.toLowerCase()) || tags.length >= 8) return tags;
  return [...tags, t];
}

/** Подпись уровня черты для UI. */
export function traitWord(v: number, t: { lo: string; hi: string }): string {
  if (v >= 67) return t.hi;
  if (v < 34) return t.lo;
  return "в меру";
}

/** Краткое описание характера одной строкой — для главной. */
export function summary(p: Profile): string {
  const parts = TRAITS.filter((t) => p.traits[t.id] >= 67).map((t) => t.hi);
  if (!parts.length) parts.push("спокойная");
  return [...parts, ...p.tags.slice(0, 2)].join(", ");
}
