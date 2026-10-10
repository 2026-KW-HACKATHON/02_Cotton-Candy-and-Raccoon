import {
  type DocumentPart,
  type GlossaryTerm,
  type Notice,
  type NoticeCategory,
  type NoticeFile,
} from "../types/notice";
import { NoticeRequestError } from "../domain/noticeError";

export const LIST_COLUMNS =
  "id,source,title,department,registered_on,display_status,category_code,deadline_on,headline,card_summaries,has_easy_text";
export const DETAIL_COLUMNS =
  LIST_COLUMNS +
  ",url,body_text,preparation_omissions,files,easy_original_text,easy_text,easy_changes,easy_body_text_present,easy_attachment_content_included,easy_result";
const CATEGORIES: Record<number, NoticeCategory> = {
  21: "교통",
  22: "안전",
  23: "주택",
  24: "경제",
  25: "환경",
  26: "문화",
  27: "복지",
  30: "행정",
};
export function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new NoticeRequestError("contract");
  return value as Record<string, unknown>;
}
function text(value: unknown) {
  return typeof value === "string" ? value : "";
}
export function httpUrl(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}
export function parseNotice(value: unknown): Notice {
  const row = record(value);
  if (
    !Number.isSafeInteger(row.id) ||
    Number(row.id) <= 0 ||
    !text(row.title) ||
    !/^\d{4}-\d{2}-\d{2}$/.test(text(row.registered_on))
  )
    throw new NoticeRequestError("contract");
  const statuses = [
    "none",
    "pending",
    "failed",
    "summarized",
    "needs_review",
  ] as const;
  const status = statuses.find((s) => s === row.display_status);
  if (!status) throw new NoticeRequestError("contract");
  const cards = row.card_summaries == null ? null : record(row.card_summaries);
  const summaryAvailable = status === "summarized" || status === "needs_review";
  const card = (key: string) =>
    summaryAvailable && cards ? text(cards[key]) || "원문에서 확인" : "";
  const files: NoticeFile[] = [];
  if (row.files !== undefined && !Array.isArray(row.files))
    throw new NoticeRequestError("contract");
  for (const raw of Array.isArray(row.files) ? row.files : []) {
    const file = record(raw);
    const url = httpUrl(file.url);
    if (
      !Number.isSafeInteger(file.id) ||
      Number(file.id) <= 0 ||
      !["attachment", "inline_image"].includes(text(file.kind)) ||
      !url
    )
      throw new NoticeRequestError("contract");
    files.push({
      id: Number(file.id),
      kind: file.kind as NoticeFile["kind"],
      url,
    });
  }
  const notice: Notice = {
    id: String(row.id),
    title: text(row.title),
    documentTitle: text(row.title),
    category: CATEGORIES[Number(row.category_code)] ?? "기타",
    provider:
      text(row.department) ||
      ({ dong: "월계1동 주민센터", nowon: "노원구청", seoul: "서울시" }[
        text(row.source)
      ] ??
        "정보제공처 미표시"),
    publishedAt: text(row.registered_on).replaceAll("-", "."),
    description: summaryAvailable ? text(row.headline) : "",
    audience: card("audience"),
    task: card("action"),
    deadline: card("deadline"),
    caution: card("notes"),
    deadlineDate:
      status === "summarized" &&
      /^\d{4}-\d{2}-\d{2}$/.test(text(row.deadline_on))
        ? text(row.deadline_on)
        : undefined,
    original: text(row.body_text),
    easy: "",
    hasEasyText: false,
    easyAttachmentContentIncluded:
      row.easy_attachment_content_included === true,
    sourceUrl: httpUrl(row.url),
    files,
    summaryStatus: status,
    omissions: objects(row.preparation_omissions).map((item) => ({
      message:
        item.reason_code === "unsupported_type"
          ? "지원하지 않는 파일 형식으로 요약에 포함되지 않았어요. 파일 원문을 확인해 주세요."
          : "읽지 못한 첨부 내용이 요약에서 빠져 있어요. 파일 원문을 확인해 주세요.",
      url: httpUrl(item.url) ?? httpUrl(row.url),
    })),
  };
  if (
    row.has_easy_text === true &&
    row.easy_body_text_present === true &&
    text(row.easy_text)
  ) {
    const original = text(row.easy_original_text);
    const easy = text(row.easy_text);
    const originalPoints = Array.from(original);
    const changes = Array.isArray(row.easy_changes)
      ? row.easy_changes.map(record)
      : [];
    const originalParts: DocumentPart[] = [];
    const easyParts: DocumentPart[] = [];
    let offset = 0;
    let valid = true;
    for (const change of changes) {
      const start = change.start,
        end = change.end;
      const word = text(change.original),
        plain = text(change.replacement);
      if (
        typeof start !== "number" ||
        typeof end !== "number" ||
        !Number.isSafeInteger(start) ||
        !Number.isSafeInteger(end) ||
        start < offset ||
        end <= start ||
        end > originalPoints.length ||
        originalPoints.slice(start, end).join("") !== word ||
        !plain
      ) {
        valid = false;
        break;
      }
      const prefix = originalPoints.slice(offset, start).join("");
      originalParts.push({ text: prefix });
      easyParts.push({ text: prefix });
      const term: GlossaryTerm = { original: word, plain };
      originalParts.push({ text: word, term });
      easyParts.push({ text: plain, term });
      offset = end;
    }
    const rest = originalPoints.slice(offset).join("");
    originalParts.push({ text: rest });
    easyParts.push({ text: rest });
    const prefix = notice.title + "\n";
    const easyTitleLength = easy.indexOf("\n") + 1;
    if (
      valid &&
      easyParts.map((p) => p.text).join("") === easy &&
      original === prefix + notice.original &&
      easyTitleLength > 0
    ) {
      // 변환 위치는 코드포인트 기준이며 제목을 제외한 본문과 정확히 일치할 때만 연결한다.
      const trimTitle = (parts: DocumentPart[], titleLength: number) => {
        let skip = titleLength;
        return parts.flatMap((part) => {
          if (skip >= part.text.length) {
            skip -= part.text.length;
            return [];
          }
          const result = { ...part, text: part.text.slice(skip) };
          skip = 0;
          return [result];
        });
      };
      notice.documentParts = {
        original: trimTitle(originalParts, prefix.length),
        easy: trimTitle(easyParts, easyTitleLength),
      };
    }
    notice.easy = easyTitleLength > 0 ? easy.slice(easyTitleLength) : easy;
    notice.hasEasyText = true;
    notice.easyIsRewrite =
      row.easy_result !== null &&
      typeof row.easy_result === "object" &&
      !Array.isArray(row.easy_result);
    if (notice.easyIsRewrite) {
      // Rewrite paragraphs have no replacement offsets. #105 may independently
      // attach dictionary candidates to the original, never to this rewritten text.
      notice.documentParts = {
        original: [{ text: notice.original }],
        easy: [{ text: notice.easy }],
      };
    }
  }
  return notice;
}

function objects(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          !!item && typeof item === "object" && !Array.isArray(item),
      )
    : [];
}
