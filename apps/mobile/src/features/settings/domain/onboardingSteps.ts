export type OnboardingTarget =
  | "scope"
  | "open"
  | "carousel"
  | "navigation"
  | "settings"
  | "save"
  | "summary"
  | "reading"
  | "word"
  | "source";
export type OnboardingStep = {
  target: OnboardingTarget;
  scene: "home" | "detail";
  label: string;
  title: string;
  description: string;
};

// Figma QYCEBzvJCSX22QZ1VmJn8Q / 482:11441, 하위 483:348~639, 조회 2026-10-09.
export const EASY_ONBOARDING: readonly OnboardingStep[] = [
  {
    target: "scope",
    scene: "home",
    label: "눌러 보세요",
    title: "우리 동네 공문을 골라요",
    description: "서울시 · 노원구 · 월계1동 중\n확인할 지역을 눌러 주세요.",
  },
  {
    target: "open",
    scene: "home",
    label: "눌러 보세요",
    title: "공문 내용을 자세히 읽어요",
    description:
      "관심 있는 공문의 ‘공문 보기’를\n누르면 상세 내용을 볼 수 있어요.",
  },
  {
    target: "navigation",
    scene: "home",
    label: "주요 메뉴",
    title: "필요한 메뉴로 바로 가요",
    description:
      "전체 공문을 둘러보거나 저장한 공문을\n다시 찾아보세요. 설정도 여기 있어요.",
  },
  {
    target: "save",
    scene: "detail",
    label: "눌러 보세요",
    title: "필요한 공문은 저장해요",
    description:
      "‘저장하기’를 눌러 두면 나중에\n‘저장한 공문’에서 다시 볼 수 있어요.",
  },
  {
    target: "summary",
    scene: "detail",
    label: "핵심 내용",
    title: "대상 · 할 일 · 기한을 확인해요",
    description:
      "핵심 내용을 먼저 읽어 보세요.\n정확한 조건은 공식 원문에서 확인해요.",
  },
];
export const STANDARD_ONBOARDING: readonly OnboardingStep[] = [
  {
    target: "scope",
    scene: "home",
    label: "눌러 보세요",
    title: "보고 싶은 지역을 선택해요",
    description:
      "지역 이름을 눌러 서울시 · 노원구 ·\n우리 동네의 공문을 바꿔 볼 수 있어요.",
  },
  {
    target: "open",
    scene: "home",
    label: "눌러 보세요",
    title: "도착한 공문을 열어 보세요",
    description:
      "봉투 속 ‘공문 보기’를 누르면\n공문의 자세한 내용으로 이동해요.",
  },
  {
    target: "carousel",
    scene: "home",
    label: "공문 둘러보기",
    title: "다른 공문도 살펴봐요",
    description:
      "화살표로 오늘의 공문을 넘겨 보세요.\n‘전체 공문 보기’에서는 모두 볼 수 있어요.",
  },
  {
    target: "navigation",
    scene: "home",
    label: "주요 메뉴",
    title: "아래 메뉴로 빠르게 이동해요",
    description: "왼쪽은 전체 공문, 가운데는 홈,\n오른쪽은 저장한 공문이에요.",
  },
  {
    target: "settings",
    scene: "home",
    label: "설정",
    title: "화면은 언제든 바꿀 수 있어요",
    description:
      "톱니바퀴를 눌러 설정을 열고\n나에게 편한 화면으로 바꿔 보세요.",
  },
  {
    target: "summary",
    scene: "detail",
    label: "핵심 내용",
    title: "긴 공문도 핵심부터 읽어요",
    description:
      "대상 · 기한 · 할 일을 먼저 확인하세요.\n오른쪽 위 책갈피로 저장할 수 있어요.",
  },
  {
    target: "reading",
    scene: "detail",
    label: "읽기 도우미",
    title: "어려우면 쉬운말로 읽어요",
    description:
      "‘원문 / 쉬운말’로 읽는 방식을 바꾸고,\n점선 단어를 눌러 뜻을 확인하세요.",
  },
  {
    target: "word",
    scene: "detail",
    label: "단어 뜻",
    title: "뜻과 예시를 함께 확인해요",
    description:
      "어려운 단어를 쉬운 설명으로 풀어 드려요.\n닫기 ×를 누르면 공문으로 돌아가요.",
  },
  {
    target: "source",
    scene: "detail",
    label: "공식 원문",
    title: "마지막으로 원문을 확인해요",
    description:
      "‘원문 파일 보기’에서 정확한 내용을\n확인하세요. AI 요약은 참고용이에요.",
  },
];
