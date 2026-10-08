import "react-native-url-polyfill/auto";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import type { Database } from "../../features/notices/api/noticeContract";

let client: SupabaseClient<Database> | undefined;
export function getSupabase(): SupabaseClient<Database> {
  if (client) return client;
  const url = process.env.EXPO_PUBLIC_SUPABASE_URL;
  const key = process.env.EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  if (!url || !key) throw new Error("앱의 Supabase 연결 설정이 없습니다.");
  if (key.startsWith("sb_secret_"))
    throw new Error("앱에는 공개 키만 설정할 수 있습니다.");
  client = createClient<Database>(url, key, {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
    },
  });
  return client;
}
