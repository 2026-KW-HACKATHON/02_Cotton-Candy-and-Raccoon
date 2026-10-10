import { type GlossaryTerm, type Notice } from "../types/notice";

export function getSummaryRows(notice: Notice) {
  return [
    { label: "대상", value: notice.audience },
    { label: "할 일", value: notice.task },
    { label: "기한", value: notice.deadline },
    { label: "유의사항", value: notice.caution },
  ].filter(({ value }) => typeof value === "string" && value.trim().length > 0);
}

/** 저장된 문구는 그대로 두고 명확한 한국어 문장 끝과 목록 앞의 공백만 줄바꿈한다. */
export function formatSummaryText(text: string): string {
  // URL·메일 주소의 경로/쿼리에 한글과 문장부호가 있어도 분리하지 않는다.
  const protectedSpans = Array.from(
    text.matchAll(/(?:https?:\/\/|www\.)\S+|[^\s@]+@[^\s@]+/gi),
    (match) => [match.index, match.index + match[0].length],
  );
  const listStarts = new Set<number>();
  // 번호는 같은 줄에 1부터 순서대로 나온 목록만 인정한다. 날짜/숫자는 추측하지 않는다.
  let lineOffset = 0;
  for (const line of text.split(/(\r\n|\n|\r)/)) {
    // 월·일 및 연·월·일 전체 구간을 제외해야 날짜의 일도 목록 번호가 되지 않는다.
    const dates = Array.from(
      line.matchAll(/\b\d{1,4}\.[ \t]*\d{1,2}\.(?:[ \t]*\d{1,2}\.)?/g),
      (match) => [match.index, match.index + match[0].length],
    );
    const markers = Array.from(
      line.matchAll(/(?:^|[ \t]+)(\((\d+)\)|(\d+)[.)])[ \t]+(?=\S)/g),
    ).filter((match) => {
      const start = match.index + match[0].indexOf(match[1]);
      return !dates.some(([from, to]) => start >= from && start < to);
    });
    if (
      markers.length > 1 &&
      markers.every(
        (match, index) => Number(match[2] ?? match[3]) === index + 1,
      )
    ) {
      for (const match of markers) {
        listStarts.add(lineOffset + match.index + match[0].indexOf(match[1]));
      }
    }
    lineOffset += line.length;
  }
  return text.replace(/[ \t]+/g, (space, offset: number) => {
    const next = offset + space.length;
    const before = text.slice(0, offset);
    // 기존 개행과 들여쓰기, 행 끝 공백은 건드리지 않는다.
    if (
      !before ||
      /[\r\n][ \t]*$/.test(before) ||
      !text[next] ||
      /[\r\n]/.test(text[next])
    ) {
      return space;
    }
    if (protectedSpans.some(([start, end]) => offset > start && offset <= end))
      return space;
    const sentenceEnd = /[가-힣][.!?]["'”’」』)]?$/.test(before);
    const bullet = /^[•●■▶※][ \t]+\S/.test(text.slice(next));
    return sentenceEnd || bullet || listStarts.has(next) ? "\n" : space;
  });
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
