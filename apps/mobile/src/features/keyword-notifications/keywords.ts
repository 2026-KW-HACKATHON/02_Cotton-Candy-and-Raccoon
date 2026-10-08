export const MAX_KEYWORDS = 10;
export const normalizeKeyword = (value: string) =>
  value.normalize("NFC").trim().replace(/\s+/gu, " ").toLowerCase();
export function addKeyword(values: string[], input: string): string[] {
  const word = normalizeKeyword(input);
  if ([...word].length < 2 || [...word].length > 30)
    throw new Error("키워드는 2~30자로 입력해 주세요.");
  if (values.includes(word)) throw new Error("이미 저장한 키워드예요.");
  if (values.length >= MAX_KEYWORDS)
    throw new Error("키워드는 최대 10개까지 저장할 수 있어요.");
  return [...values, word];
}
export function notificationNoticeId(
  data: Record<string, unknown>,
): string | null {
  if (data.type !== "keyword_notice" || typeof data.noticeId !== "string")
    return null;
  return /^[1-9][0-9]{0,18}$/.test(data.noticeId) ? data.noticeId : null;
}
