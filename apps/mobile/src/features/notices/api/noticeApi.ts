import { type Notice } from "../types/notice";

// 서버 연동 전 화면 검토용 데이터다. 실제 공고나 AI 처리 결과가 아니다.
const DEMO_NOTICES: Notice[] = [
  {
    id: "culture",
    title: "주민 문화교실 신청 안내",
    category: "주민 참여",
    description: "10월 9일까지 주민센터에서 문화교실을 신청하세요.",
    provider: "월계1동 주민센터",
    publishedAt: "2026.10.01",
    deadline: "2026.10.9",
    deadlineDate: "2026-10-09",
    audience: "월계1동 주민",
    task: "주민센터에서 문화교실 신청",
    caution: "선착순 20명",
    documentTitle: "주민 문화교실 신청 안내",
    original:
      "월계1동 주민을 대상으로 문화교실 수강 신청을 접수합니다. 2026년 10월 9일까지 주민센터에 방문하여 신청하시기 바랍니다. 모집 인원은 선착순 20명입니다.",
    easy: "월계1동 주민은 문화교실에 신청할 수 있습니다. 2026년 10월 9일까지 주민센터에 직접 가서 신청하세요. 먼저 신청한 20명이 참여할 수 있습니다.",
    terms: [
      { plain: "직접 가서", original: "방문하여" },
      { plain: "먼저 신청한", original: "선착순" },
    ],
  },
  {
    id: "idea",
    title: "우리 동네를 위한 아이디어를 모아요",
    category: "주민 참여",
    description: "주민 제안을 기다리고 있어요.",
    provider: "월계1동 주민센터",
    publishedAt: "2026. 09. 30",
    deadline: "10월 12일(월) 18:00까지",
    audience: "월계1동 주민 누구나",
    task: "제안서를 작성해 주민센터에 제출",
    caution: "마감 이후에는 접수할 수 없어요.",
    documentTitle: "2026년 월계1동 주민 제안 모집",
    original:
      "월계1동 주민 의견을 수렴하여 지역 생활환경을 개선하고자 다음과 같이 주민 제안을 접수합니다.",
    easy: "우리 동네를 더 살기 좋은 곳으로 만들 아이디어를 받아요. 좋은 생각이 있다면 제안서를 써서 주민센터에 내주세요.",
  },
  {
    id: "walk",
    title: "가을 동네 산책에 함께해요",
    category: "주민 참여",
    description: "이웃과 함께 걷는 가을 산책이에요.",
    provider: "월계1동 주민센터",
    publishedAt: "2026. 09. 29",
    deadline: "10월 15일(목)까지",
    audience: "월계1동 주민 누구나",
    task: "주민센터에서 산책 참여 신청",
    caution: "편한 신발과 물을 준비해 주세요.",
    documentTitle: "월계1동 가을 산책 참여 안내",
    original:
      "주민 간 교류 활성화를 위한 가을 산책 프로그램의 참여자를 모집합니다.",
    easy: "이웃과 함께 동네를 걸어요. 함께 걷고 싶다면 주민센터에 신청해 주세요.",
  },
  {
    id: "program",
    title: "주민센터 프로그램 참여자를 모집해요",
    category: "주민 참여",
    description: "새로운 배움을 시작해 보세요.",
    provider: "월계1동 주민센터",
    publishedAt: "2026. 09. 28",
    deadline: "10월 20일(화)까지",
    audience: "프로그램에 관심 있는 주민",
    task: "주민센터에서 프로그램 신청",
    caution: "정원이 차면 모집이 일찍 끝날 수 있어요.",
    documentTitle: "주민센터 프로그램 수강생 모집",
    original: "주민의 평생학습 기회 확대를 위한 프로그램 수강생을 모집합니다.",
    easy: "주민센터에서 새로운 것을 배워보세요. 배우고 싶은 프로그램을 골라 신청해 주세요.",
  },
];
/** 화면은 데이터 출처에 의존하지 않도록 비동기 조회 인터페이스를 사용한다. 현재는 로컬 예시를 반환한다. */
export async function fetchNotices(): Promise<Notice[]> {
  return DEMO_NOTICES;
}
/** Query가 성공한 빈 결과를 캐시할 수 있도록 없는 공문은 null로 반환한다. */
export async function fetchNotice(id: string): Promise<Notice | null> {
  return DEMO_NOTICES.find((notice) => notice.id === id) ?? null;
}
