import { fetchTodayNotice } from "../notices/api/noticeApi";
import type { Notice } from "../notices/types/notice";

export type TodayNoticeState = {
  date: string;
  status: "ready" | "empty" | "error" | "loading";
  notice?: Notice;
};

export function koreaDate(now: Date = new Date()): string {
  return new Date(now.getTime() + 9 * 60 * 60 * 1000)
    .toISOString()
    .slice(0, 10);
}

// No persisted notice cache: failed refreshes must not republish a hidden/old notice.
export async function loadTodayNotice(
  fetchNotice = fetchTodayNotice,
  clock: () => Date = () => new Date(),
): Promise<TodayNoticeState> {
  let date = koreaDate(clock());
  try {
    let notice = await fetchNotice(date);
    const after = koreaDate(clock());
    if (after !== date) {
      date = after;
      notice = await fetchNotice(date);
    }
    if (koreaDate(clock()) !== date) {
      return { date: koreaDate(clock()), status: "error" };
    }
    if (notice && notice.publishedAt.replaceAll(".", "-") !== date) {
      return { date, status: "error" };
    }
    return notice
      ? { date, status: "ready", notice }
      : { date, status: "empty" };
  } catch {
    return { date: koreaDate(clock()), status: "error" };
  }
}
