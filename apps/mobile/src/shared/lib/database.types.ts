// #58 public views only. JSON fields are checked before rendering.
export type Json =
  | null
  | boolean
  | number
  | string
  | Json[]
  | { [key: string]: Json | undefined };
export type NoticeListRow = {
  id: number;
  source: string;
  dong_group: string | null;
  is_pinned: boolean;
  title: string;
  department: string | null;
  registered_on: string;
  content_updated_at: string;
  is_modified: boolean;
  summary_status: string | null;
  display_status: string;
  notice_type: string | null;
  category_code: number | null;
  deadline_on: string | null;
  headline: string | null;
  card_summaries: Json;
  attachment_status: string | null;
  has_easy_text: boolean;
};
export type NoticeDetailRow = NoticeListRow & {
  url: string;
  license_type: string | null;
  body_text: string | null;
  result: Json;
  generated_at: string | null;
  file_references: Json;
  preparation_omissions: Json;
  files: Json;
  easy_original_text: string | null;
  easy_text: string | null;
  easy_changes: Json;
  easy_body_text_present: boolean | null;
  easy_attachment_content_included: boolean | null;
  easy_generated_at: string | null;
};
export type Database = {
  public: {
    Tables: Record<string, never>;
    Views: {
      app_notice_list: { Row: NoticeListRow; Relationships: [] };
      app_notice_detail: { Row: NoticeDetailRow; Relationships: [] };
    };
    Functions: Record<string, never>;
  };
};
