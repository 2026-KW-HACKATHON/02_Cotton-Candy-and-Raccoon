import { useMemo, useState } from "react";
import { useNotices } from "./useNotices";
import { type NoticeCategory } from "../types/notice";

export const CATEGORIES = [
  "전체",
  "교통",
  "안전",
  "주택",
  "경제",
  "환경",
  "문화",
  "복지",
  "행정",
  "기타",
] as const;
/** 조회 결과와 화면 내부 검색·필터·정렬 상태를 조합하는 Hook 기반 ViewModel이다. */
export function useNoticeListViewModel() {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<"전체" | NoticeCategory>("전체");
  const [newestFirst, setNewestFirst] = useState(true);
  const codes: Partial<Record<NoticeCategory, number>> = {
    교통: 21,
    안전: 22,
    주택: 23,
    경제: 24,
    환경: 25,
    문화: 26,
    복지: 27,
    행정: 30,
  };
  const query = useNotices(true, {
    category:
      category === "전체"
        ? undefined
        : category === "기타"
          ? null
          : codes[category],
    oldestFirst: !newestFirst,
  });
  const notices = useMemo(() => {
    const keyword = search.trim().toLocaleLowerCase();
    return (query.data ?? []).filter(
      (notice) =>
        !keyword ||
        [notice.title, notice.description].some((text) =>
          text.toLocaleLowerCase().includes(keyword),
        ),
    );
  }, [query.data, search]);
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
