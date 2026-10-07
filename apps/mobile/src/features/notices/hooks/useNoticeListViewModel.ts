import { useMemo, useState } from "react";
import { useNotices } from "./useNotices";
import { type NoticeCategory } from "../types/notice";

export const CATEGORIES = ["전체", "주민 참여", "생활", "복지"] as const;
/** 조회 결과와 화면 내부 검색·필터·정렬 상태를 조합하는 Hook 기반 ViewModel이다. */
export function useNoticeListViewModel() {
  const query = useNotices();
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<"전체" | NoticeCategory>("전체");
  const [newestFirst, setNewestFirst] = useState(true);
  // 가공한 목록만 파생시키며 Query 캐시의 원본을 별도 상태에 복사하지 않는다.
  const notices = useMemo(() => {
    const keyword = search.trim().toLocaleLowerCase();
    return (
      (query.data ?? [])
        .filter(
          (notice) =>
            (category === "전체" || notice.category === category) &&
            (!keyword ||
              [notice.title, notice.description, notice.original].some((text) =>
                text.toLocaleLowerCase().includes(keyword),
              )),
        )
        // 현재 예시 날짜는 YYYY. MM. DD 형식이다. API 연동 시 날짜 형식도 함께 확인한다.
        .toSorted((a, b) =>
          newestFirst
            ? b.publishedAt
                .replaceAll(" ", "")
                .localeCompare(a.publishedAt.replaceAll(" ", ""))
            : a.publishedAt
                .replaceAll(" ", "")
                .localeCompare(b.publishedAt.replaceAll(" ", "")),
        )
    );
  }, [query.data, search, category, newestFirst]);
  return {
    ...query,
    notices,
    search,
    setSearch,
    category,
    setCategory,
    newestFirst,
    toggleSort: () => setNewestFirst((value) => !value),
  };
}
