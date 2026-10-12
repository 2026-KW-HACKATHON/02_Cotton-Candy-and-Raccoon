import { fetchLatestNotice } from "../notices/api/noticeApi";
import type { Notice } from "../notices/types/notice";

export type LatestNoticeState = {
  date: string;
  status: "ready" | "empty" | "error" | "loading";
  notice?: Notice;
};

export function koreaDate(now: Date = new Date()): string {
  return new Date(now.getTime() + 9 * 60 * 60 * 1000)
    .toISOString()
    .slice(0, 10);
}

// 조회 실패 시 이전 결과를 재사용하지 않아 비공개로 전환된 공문이 다시 노출되지 않게 한다.
export async function loadLatestNotice(
  fetchNotice = fetchLatestNotice,
  clock: () => Date = () => new Date(),
): Promise<LatestNoticeState> {
  const date = koreaDate(clock());
  try {
    const notice = await fetchNotice();
    return notice
      ? { date: notice.publishedAt.replaceAll(".", "-"), status: "ready", notice }
      : { date, status: "empty" };
  } catch {
    return { date, status: "error" };
  }
}
