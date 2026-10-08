import type { GlossaryTerm } from "../types/notice";
export type EasyPart = { text: string; term?: GlossaryTerm };

/** Only mark validated replacement spans; never search for matching words. */
export function easyTextParts(
  original: string,
  easy: string,
  changes: unknown,
): EasyPart[] {
  const fallback = [{ text: easy }];
  if (!Array.isArray(changes)) return fallback;
  const points = Array.from(original);
  const parts: EasyPart[] = [];
  let cursor = 0;
  for (const value of changes) {
    if (!value || typeof value !== "object") return fallback;
    const {
      start,
      end,
      original: before,
      replacement,
    } = value as Record<string, unknown>;
    if (
      typeof start !== "number" ||
      !Number.isInteger(start) ||
      typeof end !== "number" ||
      !Number.isInteger(end) ||
      start < cursor ||
      end <= start ||
      end > points.length ||
      typeof before !== "string" ||
      typeof replacement !== "string" ||
      !replacement ||
      points.slice(start, end).join("") !== before
    )
      return fallback;
    if (start > cursor)
      parts.push({ text: points.slice(cursor, start).join("") });
    parts.push({
      text: replacement,
      term: { plain: replacement, original: before },
    });
    cursor = end;
  }
  if (cursor < points.length)
    parts.push({ text: points.slice(cursor).join("") });
  return parts.map((part) => part.text).join("") === easy ? parts : fallback;
}
