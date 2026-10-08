export type NoticeCategory =
  | "주민 참여"
  | "생활"
  | "복지"
  | "민방위"
  | "교통"
  | "안전"
  | "주택"
  | "경제"
  | "환경"
  | "문화"
  | "행정"
  | "기타";
export type NoticeFile = {
  id: number;
  kind: "attachment" | "inline_image";
  url: string;
};
export type DocumentPart = { text: string; term?: GlossaryTerm };
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
  sourceUrl?: string;
  files?: NoticeFile[];
  evidence?: { quote: string; label: string; url?: string }[];
  omissions?: { message: string; url?: string }[];
  hasEasyText?: boolean;
  easyAttachmentContentIncluded?: boolean;
  summaryStatus?: "none" | "pending" | "failed" | "summarized" | "needs_review";
  documentParts?: { original: DocumentPart[]; easy: DocumentPart[] };
};
