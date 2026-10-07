import { useQuery } from "@tanstack/react-query";
import { fetchNotice, fetchNotices } from "../api/noticeApi";

// 목록과 상세를 같은 키 접두사로 묶고, 상세 ID로 공문별 캐시를 구분한다.
export const NOTICE_KEYS = {
  all: ["notices"] as const,
  detail: (id: string) => ["notices", id] as const,
};
export function useNotices() {
  return useQuery({ queryKey: NOTICE_KEYS.all, queryFn: fetchNotices });
}
export function useNotice(id: string) {
  return useQuery({
    queryKey: NOTICE_KEYS.detail(id),
    queryFn: () => fetchNotice(id),
  });
}
