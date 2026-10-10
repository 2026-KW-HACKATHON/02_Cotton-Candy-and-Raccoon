import { type DocumentPart } from "../types/notice";

export type SummaryCardKey = "deadline" | "audience" | "action" | "notes";
export type EvidenceRange = { start: number; end: number };
export type SummaryEvidence = Record<SummaryCardKey, EvidenceRange[]>;
const FIELDS: Record<SummaryCardKey, readonly string[]> = {
  deadline: [
    "dates",
    "status",
    "status_detail",
    "notice_update",
    "changed_details",
  ],
  audience: ["audience", "audience_scope"],
  action: ["action", "action_requirement", "location"],
  notes: [
    "notes",
    "notice_update",
    "changed_details",
    "status_detail",
    "status",
  ],
};
export const CARD_KEYS: Record<string, SummaryCardKey> = {
  기한: "deadline",
  대상: "audience",
  "할 일": "action",
  유의사항: "notes",
};

/** Use current body UTF-16 offsets only. Never guess a location or follow a source URL. */
export function locateSummaryEvidence(
  body: string,
  result: unknown,
): SummaryEvidence {
  const ranges: SummaryEvidence = {
    deadline: [],
    audience: [],
    action: [],
    notes: [],
  };
  if (
    !result ||
    typeof result !== "object" ||
    !("evidence" in result) ||
    !Array.isArray(result.evidence)
  )
    return ranges;
  for (const item of result.evidence) {
    if (
      !item ||
      item.source_type !== "text" ||
      item.verification !== "text_matched" ||
      item.source_id != null ||
      item.page != null ||
      typeof item.excerpt !== "string" ||
      !item.excerpt.trim()
    )
      continue;
    const start = body.indexOf(item.excerpt);
    if (start < 0 || body.indexOf(item.excerpt, start + 1) >= 0) continue;
    for (const key of Object.keys(FIELDS) as SummaryCardKey[]) {
      if (FIELDS[key].includes(item.field))
        ranges[key].push({ start, end: start + item.excerpt.length });
    }
  }
  return ranges;
}

/** Split at evidence boundaries without losing glossary metadata or changing text. */
export function highlightDocumentParts(
  parts: readonly DocumentPart[],
  ranges: readonly EvidenceRange[],
): DocumentPart[] {
  let offset = 0;
  return parts.flatMap((part) => {
    const start = offset;
    offset += part.text.length;
    const boundaries = [
      ...new Set([
        start,
        offset,
        ...ranges
          .flatMap((r) => [r.start, r.end])
          .filter((n) => n > start && n < offset),
      ]),
    ].sort((a, b) => a - b);
    return boundaries.slice(0, -1).map((a, i) => ({
      ...part,
      text: part.text.slice(a - start, boundaries[i + 1] - start),
      highlighted: ranges.some(
        (r) => r.start <= a && r.end >= boundaries[i + 1],
      ),
    }));
  });
}
