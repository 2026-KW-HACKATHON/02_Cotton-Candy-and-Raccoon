import type { GlossaryTerm, Notice } from "../types/notice";
export type EasyPart = { text: string; term?: GlossaryTerm };

/** Only mark validated replacement spans; never search for matching words. */
export function easyTextParts(
  original: string,
  easy: string,
  changes: unknown,
  mode: "plain" | "original" = "plain",
): EasyPart[] {
  const fallback = [{ text: mode === "original" ? original : easy }];
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
  if (parts.map((part) => part.text).join("") !== easy) return fallback;
  return mode === "original"
    ? parts.map((part) => ({ ...part, text: part.term?.original ?? part.text }))
    : parts;
}

/** 현재 본문과 변환 원문이 일치할 때만 원문에도 변환 위치를 연결한다. */
export function noticeDocumentParts(
  notice: Pick<
    Notice,
    "original" | "easy" | "hasEasyText" | "easyOriginal" | "easyChanges"
  >,
  easy: boolean,
): EasyPart[] {
  const hasEasyText = notice.hasEasyText && !!notice.easy;
  if (easy && hasEasyText) {
    return easyTextParts(notice.easyOriginal, notice.easy, notice.easyChanges);
  }
  if (hasEasyText && notice.original === notice.easyOriginal) {
    return easyTextParts(
      notice.easyOriginal,
      notice.easy,
      notice.easyChanges,
      "original",
    );
  }
  return [
    {
      text:
        notice.original ||
        "본문 텍스트가 없습니다. 원문과 첨부를 확인해 주세요.",
    },
  ];
}
