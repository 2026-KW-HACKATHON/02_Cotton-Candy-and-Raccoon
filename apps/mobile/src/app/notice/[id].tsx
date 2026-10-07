import { fetchNotices } from "@/features/notices/api/noticeApi";

export { NoticeDetailScreen as default } from "@/features/notices/screens/NoticeDetailScreen";

// 정적 웹 출력에서도 예시 상세 URL의 직접 접속·새로고침이 가능하도록 경로를 생성한다.
export async function generateStaticParams() {
  const notices = await fetchNotices();
  return notices.map(({ id }) => ({ id }));
}
