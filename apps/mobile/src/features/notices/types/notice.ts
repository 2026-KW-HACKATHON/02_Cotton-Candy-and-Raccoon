export const CATEGORY_LABELS = {
  21: "교통",
  22: "안전",
  23: "주택",
  24: "경제",
  25: "환경",
  26: "문화",
  27: "복지",
  30: "행정",
} as const;
export type NoticeCategory =
  (typeof CATEGORY_LABELS)[keyof typeof CATEGORY_LABELS] | "미분류";
export type NoticeSource = "nowon" | "dong" | "seoul";
export type DisplayStatus =
  "none" | "summarized" | "needs_review" | "pending" | "failed";
export type GlossaryTerm = {
  plain: string;
  original: string;
  meaning?: string;
  example?: string;
};
export type Notice = {
  id: string;
  title: string;
  category: NoticeCategory;
  description: string;
  provider: string;
  publishedAt: string;
  deadline: string;
  audience: string;
  task: string;
  caution: string;
  documentTitle: string;
  original: string;
  easy: string;
  deadlineDate?: string;
  terms?: GlossaryTerm[];
  source: NoticeSource;
  categoryCode: number | null;
  registeredOn: string;
  displayStatus: DisplayStatus;
  hasEasyText: boolean;
  easyOriginal: string;
  easyChanges: unknown;
  hasSummary: boolean;
  url: string;
  generatedAt: string | null;
  attachmentStatus: string | null;
  attachmentContentIncluded: boolean;
  omissions: string[];
  evidence: { quote: string; url?: string; label: string }[];
  files: { id: string; kind: string; url: string }[];
};
