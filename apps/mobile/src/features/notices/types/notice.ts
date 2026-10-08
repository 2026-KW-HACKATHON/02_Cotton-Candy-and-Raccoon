export type NoticeCategory = "주민 참여" | "생활" | "복지" | "민방위";
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
};
