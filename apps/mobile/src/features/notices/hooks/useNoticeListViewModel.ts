import { useMemo, useState } from "react";
import { useNotices } from "./useNotices";
import { useNoticeScopeStore } from "../store/noticeScopeStore";
import { CATEGORY_LABELS, type NoticeCategory } from "../types/notice";

export const CATEGORIES: readonly ("전체" | NoticeCategory)[] = [
  "전체",
  ...Object.values(CATEGORY_LABELS),
  "미분류",
];
export function useNoticeListViewModel() {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<"전체" | NoticeCategory>("전체");
  const source = useNoticeScopeStore((state) => state.source);
  const setSource = useNoticeScopeStore((state) => state.setSource);
  const [newestFirst, setNewestFirst] = useState(true);
  const code = Object.entries(CATEGORY_LABELS).find(
    ([, label]) => label === category,
  )?.[0];
  const query = useNotices({
    source,
    ascending: !newestFirst,
    category:
      category === "미분류" ? "unclassified" : code ? Number(code) : undefined,
  });
  // Search deliberately covers loaded titles/summaries; body text is detail-only.
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
    source,
    setSource,
    newestFirst,
    toggleSort: () => setNewestFirst((value) => !value),
  };
}
