import {
  useInfiniteQuery,
  useQuery,
  type InfiniteData,
} from "@tanstack/react-query";
import {
  fetchNotice,
  fetchNoticeDictionary,
  fetchNoticePage,
  fetchSavedNotices,
  type NoticeCursor,
  type NoticeListOptions,
} from "../api/noticeApi";
import { withNoticeDictionary } from "../domain/noticeDictionary";
import { NoticeRequestError } from "../domain/noticeError";
import { useNoticeScopeStore } from "../store/noticeScopeStore";

// 목록과 상세를 같은 키 접두사로 묶고, 상세 ID로 공문별 캐시를 구분한다.
export const NOTICE_KEYS = {
  all: ["notices"] as const,
  detail: (id: string) => ["notices", id] as const,
};
const selectNotices = (
  data: InfiniteData<
    Awaited<ReturnType<typeof fetchNoticePage>>,
    NoticeCursor | null
  >,
) => data.pages.flatMap((page) => page.notices);

export function useNotices(enabled = true, options: NoticeListOptions = {}) {
  const scope = useNoticeScopeStore((state) => state.scope);
  const source =
    scope === "서울시" ? "seoul" : scope === "노원구" ? "nowon" : "dong";
  return useInfiniteQuery({
    enabled,
    queryKey: [
      ...NOTICE_KEYS.all,
      "list",
      source,
      options.category === undefined ? "all" : options.category,
      !!options.oldestFirst,
    ],
    initialPageParam: null as NoticeCursor | null,
    queryFn: ({ pageParam }) => fetchNoticePage(pageParam, source, options),
    getNextPageParam: (page) => page.nextCursor,
    select: selectNotices,
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
  const query = useQuery({
    queryKey: NOTICE_KEYS.detail(id),
    queryFn: () => fetchNotice(id),
    retry: (count, error) =>
      !(error instanceof NoticeRequestError && error.code !== "connection") &&
      count < 1,
  });
  const notice = query.data;
  const dictionary = useQuery({
    queryKey: [
      ...NOTICE_KEYS.detail(id),
      "dictionary",
      notice?.title,
      notice?.original,
    ],
    enabled: !!notice?.hasEasyText,
    queryFn: () => fetchNoticeDictionary(id),
    retry: (count, error) =>
      error instanceof NoticeRequestError &&
      error.code === "connection" &&
      count < 1,
    refetchInterval: (dictionaryQuery) => {
      if (!notice?.hasEasyText || dictionaryQuery.state.status === "error")
        return false;
      const current = withNoticeDictionary(notice, dictionaryQuery.state.data);
      return current.dictionaryStatus === "pending" ||
        current.documentParts?.original.some(
          (part) => part.term?.dictionary?.status === "pending",
        )
        ? 10_000
        : false;
    },
    refetchIntervalInBackground: false,
  });
  return {
    ...query,
    refetch: async (...args: Parameters<typeof query.refetch>) => {
      const [result] = await Promise.all([
        query.refetch(...args),
        notice?.hasEasyText ? dictionary.refetch() : Promise.resolve(),
      ]);
      return result;
    },
    data: notice
      ? withNoticeDictionary(
          notice,
          dictionary.data,
          !notice.hasEasyText
            ? "unprocessed"
            : dictionary.isError
              ? "failed"
              : dictionary.isPending
                ? "loading"
                : "complete",
        )
      : notice,
  };
}
