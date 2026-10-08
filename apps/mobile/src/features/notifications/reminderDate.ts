export const REMINDER_KIND = "notice-deadline-v1";

export function reminderIdentifier(noticeId: string): string {
  if (!/^[1-9]\d*$/.test(noticeId))
    throw new Error("공지 번호를 확인해 주세요.");
  return `${REMINDER_KIND}:${noticeId}`;
}

/** Interpret user input in the device's local timezone, never as UTC midnight. */
export function parseReminderDate(date: string, time: string): Date {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^\d{2}:\d{2}$/.test(time))
    throw new Error("날짜는 YYYY-MM-DD, 시간은 HH:mm 형식으로 입력해 주세요.");
  const [year, month, day] = date.split("-").map(Number);
  const [hour, minute] = time.split(":").map(Number);
  const result = new Date(year, month - 1, day, hour, minute);
  if (
    year < 2000 ||
    result.getFullYear() !== year ||
    result.getMonth() !== month - 1 ||
    result.getDate() !== day ||
    result.getHours() !== hour ||
    result.getMinutes() !== minute
  )
    throw new Error("실제로 존재하는 날짜와 시간을 입력해 주세요.");
  return result;
}

export function dateInput(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

export function presetDate(deadline: string, daysBefore: number): string {
  const date = parseReminderDate(deadline, "12:00");
  date.setDate(date.getDate() - daysBefore);
  return dateInput(date);
}

export function reminderNoticeId(
  data: Record<string, unknown> | undefined,
): string | null {
  return data?.kind === REMINDER_KIND &&
    typeof data.noticeId === "string" &&
    /^[1-9]\d*$/.test(data.noticeId)
    ? data.noticeId
    : null;
}
