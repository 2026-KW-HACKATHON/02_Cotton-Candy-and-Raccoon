import "react-native-url-polyfill/auto";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { authStorage } from "./authStorage";

export type KeywordPreferences = { enabled: boolean; keywords: string[] };
let client: SupabaseClient | undefined;
let signingIn: Promise<void> | undefined;
function getClient() {
  if (client) return client;
  const url = process.env.EXPO_PUBLIC_SUPABASE_URL;
  const key = process.env.EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  if (!url || !key || key.startsWith("sb_secret_"))
    throw new Error("알림 연결 설정을 확인해 주세요.");
  client = createClient(url, key, {
    auth: {
      storage: authStorage,
      storageKey: "wolgyenotice-keyword-auth",
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: false,
    },
  });
  return client;
}
async function authenticated() {
  const db = getClient();
  signingIn ??= (async () => {
    const { data, error } = await db.auth.getSession();
    if (error)
      throw new Error("기기 인증을 확인하지 못했어요. 다시 시도해 주세요.");
    if (!data.session) {
      const result = await db.auth.signInAnonymously();
      if (result.error)
        throw new Error(
          "알림 등록에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.",
        );
    }
  })().finally(() => {
    signingIn = undefined;
  });
  await signingIn;
  return db;
}
function preferences(data: unknown): KeywordPreferences {
  const value = data as KeywordPreferences | null;
  if (
    !value ||
    typeof value.enabled !== "boolean" ||
    !Array.isArray(value.keywords) ||
    value.keywords.some((word) => typeof word !== "string")
  )
    throw new Error("저장된 키워드를 확인하지 못했어요.");
  return value;
}
export async function loadKeywords(): Promise<KeywordPreferences> {
  const db = await authenticated();
  const { data, error } = await db.rpc("get_keyword_preferences");
  if (error) throw new Error("키워드를 불러오지 못했어요. 다시 시도해 주세요.");
  return preferences(data);
}
export async function saveKeywords(
  value: KeywordPreferences,
  token: string | null,
) {
  const db = await authenticated();
  const { data, error } = await db.rpc("save_keyword_preferences", {
    p_keywords: value.keywords,
    p_enabled: value.enabled,
    p_token: token,
  });
  if (error)
    throw new Error(
      error.message.includes("update_rate_limited")
        ? "잠시 기다린 뒤 다시 저장해 주세요."
        : "저장하지 못했어요. 연결을 확인하고 다시 시도해 주세요.",
    );
  return preferences(data);
}

export async function refreshPushRegistration() {
  const db = getClient();
  const { data } = await db.auth.getSession();
  if (!data.session) return;
  const { data: preferences } = await db.rpc("get_keyword_preferences");
  if (!preferences?.enabled) return;
  const token = await (await import("./push")).pushToken(false);
  await db.rpc("refresh_keyword_push_token", { p_token: token });
}
