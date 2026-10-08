import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import {
  fetchNotice,
  fetchNoticePage,
  fetchSavedNotices,
  type NoticeCursor,
} from "../api/noticeApi";
import { NoticeRequestError } from "../domain/noticeError";
import { useNoticeScopeStore } from "../store/noticeScopeStore";

// 목록과 상세를 같은 키 접두사로 묶고, 상세 ID로 공문별 캐시를 구분한다.
export const NOTICE_KEYS = {
  all: ["notices"] as const,
  detail: (id: string) => ["notices", id] as const,
};
export function useNotices(enabled = true) {
  const scope = useNoticeScopeStore((state) => state.scope);
  const source =
    scope === "서울시" ? "seoul" : scope === "노원구" ? "nowon" : "dong";
  return useInfiniteQuery({
    enabled,
    queryKey: [...NOTICE_KEYS.all, "list", source],
    initialPageParam: null as NoticeCursor | null,
    queryFn: ({ pageParam }) => fetchNoticePage(pageParam, source),
    getNextPageParam: (page) => page.nextCursor,
    select: (data) => data.pages.flatMap((page) => page.notices),
    retry: (count, error) =>
      !(error instanceof NoticeRequestError && error.code !== "connection") &&
      count < 1,
  });
}
export function useSavedNotices(ids: readonly string[], enabled = true) {
  return useQuery({
    enabled,
    queryKey: [...NOTICE_KEYS.all, "saved", ids],
    queryFn: () => fetchSavedNotices(ids),
    retry: false,
  });
}
export function useNotice(id: string) {
  return useQuery({
    queryKey: NOTICE_KEYS.detail(id),
    queryFn: () => fetchNotice(id),
    retry: (count, error) =>
      !(error instanceof NoticeRequestError && error.code !== "connection") &&
      count < 1,
  });
}
