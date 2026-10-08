import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import {
  fetchNotice,
  fetchNotices,
  fetchSavedNotices,
  type Cursor,
  type ListOptions,
} from "../api/noticeApi";
import { useBookmarkStore } from "../store/bookmarkStore";

export const NOTICE_KEYS = {
  all: ["notices"] as const,
  list: (options: ListOptions) => ["notices", "list", options] as const,
  detail: (id: string) => ["notices", "detail", id] as const,
};
export function useNotices(options: ListOptions = {}, enabled = true) {
  const query = useInfiniteQuery({
    queryKey: NOTICE_KEYS.list(options),
    initialPageParam: null as Cursor | null,
    queryFn: ({ pageParam, signal }) =>
      fetchNotices(options, pageParam, signal),
    getNextPageParam: (page) => page.next,
    retry: false,
    enabled,
  });
  const rows = query.data?.pages.flatMap((page) => page.notices);
  return {
    ...query,
    data: rows
      ? [...new Map(rows.map((row) => [row.id, row])).values()]
      : undefined,
  };
}
export function useSavedNotices(enabled = true) {
  const ids = useBookmarkStore((state) => state.savedIds);
  return useQuery({
    queryKey: ["notices", "saved", ids],
    queryFn: ({ signal }) => fetchSavedNotices(ids, signal),
    retry: false,
    enabled,
  });
}
export function useNotice(id: string) {
  return useQuery({
    queryKey: NOTICE_KEYS.detail(id),
    queryFn: ({ signal }) => fetchNotice(id, signal),
    retry: false,
  });
}
