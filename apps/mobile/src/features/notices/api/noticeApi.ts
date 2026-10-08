import { type Notice } from "../types/notice";
import { DETAIL_COLUMNS, LIST_COLUMNS, parseNotice } from "./noticeContract";
import { NoticeRequestError } from "../domain/noticeError";

export type NoticeCursor = { date: string; id: string };
export type NoticeListOptions = {
  category?: number | null;
  oldestFirst?: boolean;
};
const PAGE_SIZE = 20;
function connection() {
  const url = process.env.EXPO_PUBLIC_SUPABASE_URL;
  const key = process.env.EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  if (!url || !key || !key.startsWith("sb_publishable_"))
    throw new NoticeRequestError("configuration");
  try {
    const parsed = new URL(url);
    if (
      !["https:", "http:"].includes(parsed.protocol) ||
      parsed.username ||
      parsed.password
    )
      throw new Error();
  } catch {
    throw new NoticeRequestError("configuration");
  }
  return { url: url.replace(/\/$/, ""), key };
}
async function request(
  view: "app_notice_list" | "app_notice_detail",
  params: URLSearchParams,
  timeoutMs = 15_000,
): Promise<unknown[]> {
  const { url, key } = connection();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(
      url + "/rest/v1/" + view + "?" + params.toString(),
      { headers: { apikey: key }, signal: controller.signal },
    );
    if (!response.ok)
      throw new NoticeRequestError(
        response.status === 401 || response.status === 403
          ? "configuration"
          : response.status === 400 || response.status === 404
            ? "contract"
            : "connection",
      );
    const data: unknown = await response.json();
    if (!Array.isArray(data)) throw new NoticeRequestError("contract");
    return data;
  } catch (error) {
    if (error instanceof NoticeRequestError) throw error;
    throw new NoticeRequestError("connection");
  } finally {
    clearTimeout(timer);
  }
}
export async function fetchNoticePage(
  cursor: NoticeCursor | null = null,
  source?: "dong" | "nowon" | "seoul",
  options: NoticeListOptions = {},
) {
  const params = new URLSearchParams({
    select: LIST_COLUMNS,
    order: options.oldestFirst
      ? "registered_on.asc,id.asc"
      : "registered_on.desc,id.desc",
    limit: String(PAGE_SIZE),
  });
  if (options.category !== undefined) {
    if (
      options.category !== null &&
      ![21, 22, 23, 24, 25, 26, 27, 30].includes(options.category)
    )
      throw new NoticeRequestError("contract");
    params.set(
      "category_code",
      options.category === null ? "is.null" : "eq." + options.category,
    );
  }
  const comparison = options.oldestFirst ? "gt" : "lt";
  if (source) params.set("source", "eq." + source);
  if (cursor) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(cursor.date) || !/^\d+$/.test(cursor.id))
      throw new NoticeRequestError("contract");
    params.set(
      "or",
      `(registered_on.${comparison}.${cursor.date},and(registered_on.eq.${cursor.date},id.${comparison}.${cursor.id}))`,
    );
  }
  const notices = (await request("app_notice_list", params)).map(parseNotice);
  const last = notices[notices.length - 1];
  return {
    notices,
    nextCursor:
      notices.length === PAGE_SIZE && last
        ? { date: last.publishedAt.replaceAll(".", "-"), id: last.id }
        : null,
  };
}
export async function fetchNotices(): Promise<Notice[]> {
  return (await fetchNoticePage()).notices;
}
/** Public notice for today's Korean calendar date, regardless of AI readiness. */
export async function fetchTodayNotice(date: string): Promise<Notice | null> {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new NoticeRequestError("contract");
  const rows = await request("app_notice_list", new URLSearchParams({
    select: LIST_COLUMNS,
    registered_on: "eq." + date,
    order: "registered_on.desc,id.desc",
    limit: "1",
  }), 8_000);
  return rows.length ? parseNotice(rows[0]) : null;
}
export async function fetchNotice(id: string): Promise<Notice | null> {
  if (!/^\d+$/.test(id) || Number(id) <= 0 || !Number.isSafeInteger(Number(id)))
    return null;
  const rows = await request(
    "app_notice_detail",
    new URLSearchParams({ select: DETAIL_COLUMNS, id: "eq." + id, limit: "1" }),
  );
  return rows.length ? parseNotice(rows[0]) : null;
}
export async function fetchSavedNotices(
  ids: readonly string[],
): Promise<Notice[]> {
  const valid = [...new Set(ids)].filter(
    (id) =>
      /^\d+$/.test(id) && Number.isSafeInteger(Number(id)) && Number(id) > 0,
  );
  const results: Notice[] = [];
  for (let offset = 0; offset < valid.length; offset += PAGE_SIZE) {
    const rows = await request(
      "app_notice_list",
      new URLSearchParams({
        select: LIST_COLUMNS,
        id: "in.(" + valid.slice(offset, offset + PAGE_SIZE).join(",") + ")",
      }),
    );
    results.push(...rows.map(parseNotice));
  }
  return results;
}
