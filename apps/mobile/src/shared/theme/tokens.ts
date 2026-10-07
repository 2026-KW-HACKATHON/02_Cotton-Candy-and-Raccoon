// Figma 일반 화면에서 여러 기능이 함께 사용하는 색상·간격·글꼴 값이다.
export const COLORS = {
  surface: "#FEFDFB",
  soft: "#EAF3FA",
  text: "#243B53",
  secondary: "#52677D",
  primary: "#25639B",
  border: "#C7D6E3",
  accent: "#D98573",
};
export const SPACE = {
  xs: 4,
  sm: 8,
  md: 11,
  lg: 15,
  xl: 18.462,
  xxl: 22,
  xxxl: 30,
};
export const RADIUS = {
  badge: 7.385,
  control: 11.077,
  card: 18.462,
  pill: 999,
};
export const FONTS = {
  display: "Jua",
  body: "NotoSansKR",
  medium: "NotoSansKRMedium",
  bold: "NotoSansKRBold",
};
export const CARD_SHADOW = {
  shadowColor: COLORS.text,
  shadowOffset: { width: 0, height: 4 },
  shadowOpacity: 0.06,
  shadowRadius: 4,
  elevation: 2,
};

// 편한 화면은 일반 화면의 색상을 공유하고 큰 글자와 세로 간격을 사용한다.
export const EASY = {
  body: 20,
  title: 28,
  heading: 24,
  inset: 20,
  gap: 20,
  cardRadius: 20,
  buttonRadius: 12,
  buttonHeight: 56,
  muted: "#F0F2F4",
  disabled: "#E4E9ED",
  expired: "#855B1E",
  brandPoint: "#F26454",
};

// 내비게이션과 플로팅 설정의 공통 표면이다.
export const FLOATING_SURFACE = {
  backgroundColor: "rgba(254,253,251,0.96)",
  shadowColor: COLORS.text,
  shadowOpacity: 0.12,
  shadowOffset: { width: 0, height: 6 },
  shadowRadius: 6,
  elevation: 6,
};
