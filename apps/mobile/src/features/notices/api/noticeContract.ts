import {
  CATEGORY_LABELS,
  type DisplayStatus,
  type Notice,
  type NoticeSource,
} from "../types/notice";

export const LIST_COLUMNS =
  "id,source,dong_group,is_pinned,title,department,registered_on,content_updated_at,is_modified,summary_status,display_status,notice_type,category_code,deadline_on,headline,card_summaries,attachment_status,has_easy_text";
export const DETAIL_COLUMNS = `${LIST_COLUMNS},url,license_type,body_text,result,generated_at,file_references,preparation_omissions,files,easy_original_text,easy_text,easy_changes,easy_body_text_present,easy_attachment_content_included,easy_generated_at`;
export const SOURCE_LABELS = {
  dong: "월계1동",
  nowon: "노원구",
  seoul: "서울시",
} as const;
export function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
const text = (value: unknown) => (typeof value === "string" ? value : "");
const array = (value: unknown): unknown[] =>
  Array.isArray(value) ? value : [];
export function safeUrl(value: unknown): string {
  if (typeof value !== "string") return "";
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : "";
  } catch {
    return "";
  }
}
export function noticeFromRow(value: unknown): Notice {
  const row = record(value);
  if (
    !Number.isSafeInteger(row.id) ||
    (row.id as number) <= 0 ||
    typeof row.title !== "string" ||
    typeof row.registered_on !== "string" ||
    !/^\d{4}-\d{2}-\d{2}$/.test(row.registered_on) ||
    !Object.hasOwn(SOURCE_LABELS, text(row.source)) ||
    !["none", "summarized", "needs_review", "pending", "failed"].includes(
      text(row.display_status),
    )
  ) {
    throw new Error("공지 응답 형식을 확인할 수 없습니다.");
  }
  const cards = record(row.card_summaries);
  const result = record(row.result);
  const source = row.source as NoticeSource;
  const categoryCode =
    typeof row.category_code === "number" ? row.category_code : null;
  const references = array(row.file_references).map(record);
  return {
    id: String(row.id),
    title: row.title,
    source,
    categoryCode,
    category:
      CATEGORY_LABELS[categoryCode as keyof typeof CATEGORY_LABELS] ?? "미분류",
    description: text(row.headline),
    provider: text(row.department) || SOURCE_LABELS[source],
    registeredOn: row.registered_on,
    publishedAt: row.registered_on.replaceAll("-", "."),
    deadline: text(cards.deadline) || "원문에서 확인",
    audience: text(cards.audience) || "원문에서 확인",
    task: text(cards.action) || "원문에서 확인",
    caution: text(cards.notes) || "원문에서 확인",
    documentTitle: row.title,
    original: text(row.body_text),
    easy: text(row.easy_text),
    displayStatus: row.display_status as DisplayStatus,
    hasSummary:
      Object.keys(result).length > 0 ||
      Object.values(cards).some((v) => typeof v === "string" && v.trim()),
    hasEasyText: row.has_easy_text === true,
    url: safeUrl(row.url),
    easyOriginal: text(row.easy_original_text),
    easyChanges: row.easy_changes,
    generatedAt: typeof row.generated_at === "string" ? row.generated_at : null,
    attachmentStatus:
      typeof row.attachment_status === "string" ? row.attachment_status : null,
    attachmentContentIncluded: row.easy_attachment_content_included === true,
    omissions: array(row.preparation_omissions).map((v) => {
      const item = record(v);
      return (
        text(item.message) ||
        text(item.reason) ||
        "요약에 포함되지 않은 첨부가 있습니다. 원문을 확인해 주세요."
      );
    }),
    evidence: array(result.evidence)
      .map((v) => {
        const item = record(v);
        const reference = references.find(
          (ref) =>
            ref.source_id === item.source_id &&
            ref.source_type === item.source_type,
        );
        const file = record(array(reference?.files)[0]);
        return {
          quote: text(item.excerpt),
          label:
            item.source_type === "text"
              ? "본문 근거"
              : `첨부 근거${typeof item.page === "number" ? ` · ${item.page}쪽` : ""}`,
          url:
            safeUrl(file.url) ||
            safeUrl(reference?.original_notice_url) ||
            undefined,
        };
      })
      .filter((v) => v.quote),
    files: array(row.files)
      .map((v) => {
        const item = record(v);
        return {
          id: String(item.id),
          kind: text(item.kind),
          url: safeUrl(item.url),
        };
      })
      .filter((v) => v.url),
  };
}
