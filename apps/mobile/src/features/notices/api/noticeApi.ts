import { getSupabase } from "../../../shared/lib/supabase";
import { DETAIL_COLUMNS, LIST_COLUMNS, noticeFromRow } from "./noticeContract";
import type { Notice, NoticeSource } from "../types/notice";

export type ListOptions = {
  source?: NoticeSource;
  category?: number | "unclassified";
  ascending?: boolean;
};
export type Cursor = { date: string; id: string };
export type NoticePage = { notices: Notice[]; next: Cursor | null };
const PAGE_SIZE = 20;

async function request<T>(
  execute: (
    signal: AbortSignal,
  ) => PromiseLike<{
    data: T;
    error: { code?: string } | null;
    status: number;
  }>,
  signal?: AbortSignal,
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) abort();
  const timer = setTimeout(abort, 15_000);
  try {
    const response = await execute(controller.signal);
    if (response.error) {
      if ([401, 403].includes(response.status))
        throw new Error("공지 조회 권한 또는 공개 키 설정을 확인해 주세요.");
      throw new Error(
        "공문을 불러오지 못했습니다. 연결 상태를 확인하고 다시 시도해 주세요.",
      );
    }
    return response.data;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}
export async function fetchNotices(
  options: ListOptions = {},
  cursor: Cursor | null = null,
  signal?: AbortSignal,
): Promise<NoticePage> {
  let query = getSupabase()
    .from("app_notice_list")
    .select(LIST_COLUMNS)
    .order("registered_on", { ascending: !!options.ascending })
    .order("id", { ascending: !!options.ascending })
    .limit(PAGE_SIZE);
  if (options.source) query = query.eq("source", options.source);
  if (options.category === "unclassified")
    query = query.is("category_code", null);
  else if (options.category !== undefined)
    query = query.eq("category_code", options.category);
  if (cursor) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(cursor.date) || !/^\d+$/.test(cursor.id))
      throw new Error("잘못된 페이지 위치입니다.");
    const op = options.ascending ? "gt" : "lt";
    query = query.or(
      `registered_on.${op}.${cursor.date},and(registered_on.eq.${cursor.date},id.${op}.${cursor.id})`,
    );
  }
  const rows = await request((sig) => query.abortSignal(sig), signal);
  if (!Array.isArray(rows))
    throw new Error("공지 목록 응답 형식을 확인할 수 없습니다.");
  const notices = rows.map(noticeFromRow);
  const last = notices.at(-1);
  return {
    notices,
    next:
      notices.length === PAGE_SIZE && last
        ? { date: last.registeredOn, id: last.id }
        : null,
  };
}
export async function fetchNotice(
  id: string,
  signal?: AbortSignal,
): Promise<Notice | null> {
  if (!/^[1-9]\d*$/.test(id)) return null;
  const row = await request(
    (sig) =>
      getSupabase()
        .from("app_notice_detail")
        .select(DETAIL_COLUMNS)
        .eq("id", id)
        .abortSignal(sig)
        .maybeSingle(),
    signal,
  );
  return row === null ? null : noticeFromRow(row);
}
export async function fetchSavedNotices(
  ids: readonly string[],
  signal?: AbortSignal,
): Promise<Notice[]> {
  const valid = [...new Set(ids.filter((id) => /^[1-9]\d*$/.test(id)))];
  const notices: Notice[] = [];
  for (let offset = 0; offset < valid.length; offset += PAGE_SIZE) {
    const rows = await request(
      (sig) =>
        getSupabase()
          .from("app_notice_list")
          .select(LIST_COLUMNS)
          .in("id", valid.slice(offset, offset + PAGE_SIZE))
          .abortSignal(sig),
      signal,
    );
    if (!Array.isArray(rows))
      throw new Error("보관함 응답 형식을 확인할 수 없습니다.");
    notices.push(...rows.map(noticeFromRow));
  }
  return notices;
}
