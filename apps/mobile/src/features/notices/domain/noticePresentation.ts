import { type GlossaryTerm, type Notice } from "../types/notice";

export function getSummaryRows(notice: Notice) {
  return [
    { label: "대상", value: notice.audience },
    { label: "할 일", value: notice.task },
    { label: "기한", value: notice.deadline },
    { label: "유의사항", value: notice.caution },
  ].filter(({ value }) => typeof value === "string" && value.trim().length > 0);
}

/** 표시 문구를 파싱하지 않고 API의 명시적 날짜가 있을 때만 종료 여부를 판단한다. */
export function isNoticeExpired(
  notice: Pick<Notice, "deadlineDate">,
  today = new Date(),
) {
  const deadline = notice.deadlineDate;
  if (!deadline || !/^\d{4}-\d{2}-\d{2}$/.test(deadline)) return false;
  const parsed = new Date(`${deadline}T00:00:00Z`);
  if (
    Number.isNaN(parsed.getTime()) ||
    parsed.toISOString().slice(0, 10) !== deadline
  )
    return false;
  const localDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  return deadline < localDate;
}

/** 데이터가 제공한 표현만 연결하며, 겹치는 표현은 가장 긴 일치 구간을 우선한다. */
export function splitGlossaryText(
  text: string,
  terms: readonly GlossaryTerm[] = [],
  mode: "plain" | "original" = "plain",
) {
  const parts: { text: string; term?: GlossaryTerm }[] = [];
  const validTerms = [...terms]
    .filter((term) => term.plain.trim() && term.original.trim())
    .sort((a, b) => b[mode].length - a[mode].length);
  let offset = 0;
  while (offset < text.length) {
    let next = -1;
    let match: GlossaryTerm | undefined;
    for (const term of validTerms) {
      const index = text.indexOf(term[mode], offset);
      if (index >= 0 && (next < 0 || index < next)) {
        next = index;
        match = term;
      }
    }
    if (next < 0 || !match) {
      parts.push({ text: text.slice(offset) });
      break;
    }
    if (next > offset) parts.push({ text: text.slice(offset, next) });
    parts.push({ text: match[mode], term: match });
    offset = next + match[mode].length;
  }
  return parts;
}
