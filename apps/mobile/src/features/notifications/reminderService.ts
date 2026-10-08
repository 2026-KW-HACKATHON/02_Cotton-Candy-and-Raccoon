import { Platform } from "react-native";
import { REMINDER_KIND, reminderIdentifier } from "./reminderDate";

const CHANNEL = "notice-deadlines";
async function notifications() {
  if (Platform.OS !== "android")
    throw new Error("마감 알림은 Android 설치 앱에서 사용할 수 있어요.");
  return import("expo-notifications");
}

export async function getReminder(noticeId: string): Promise<Date | null> {
  const api = await notifications();
  const identifier = reminderIdentifier(noticeId);
  const request = (await api.getAllScheduledNotificationsAsync()).find(
    (item) => item.identifier === identifier,
  );
  const timestamp = request?.content.data?.scheduledAt;
  if (
    typeof timestamp !== "number" ||
    !Number.isFinite(new Date(timestamp).getTime())
  )
    return null;
  return new Date(timestamp);
}

export async function scheduleReminder(
  noticeId: string,
  title: string,
  date: Date,
): Promise<void> {
  const identifier = reminderIdentifier(noticeId);
  if (!Number.isFinite(date.getTime()) || date.getTime() <= Date.now())
    throw new Error("현재 시각 이후로 알림을 설정해 주세요.");
  const api = await notifications();
  // Android 13 permission prompt requires a channel first.
  await api.setNotificationChannelAsync(CHANNEL, {
    name: "공지 마감 알림",
    importance: api.AndroidImportance.HIGH,
    sound: "default",
    vibrationPattern: [0, 250, 250, 250],
  });
  const channel = await api.getNotificationChannelAsync(CHANNEL);
  if (channel?.importance === api.AndroidImportance.NONE)
    throw new Error("마감 알림 채널이 꺼져 있어요. 휴대폰 설정에서 켜 주세요.");
  let permission = await api.getPermissionsAsync();
  if (!permission.granted && permission.canAskAgain)
    permission = await api.requestPermissionsAsync();
  if (!permission.granted)
    throw new Error(
      "알림 권한이 필요해요. 휴대폰 설정에서 알림을 허용해 주세요.",
    );
  if (date.getTime() <= Date.now())
    throw new Error("예약 시각이 지났어요. 시간을 다시 선택해 주세요.");
  // Expo Android persists requests and uses identifier-based PendingIntents.
  // Reusing this identifier replaces the alarm without a cancel-before-save gap.
  await api.scheduleNotificationAsync({
    identifier,
    content: {
      title: "보관해 두신 공지가 있어요.",
      body: `${title}\n마감일이 임박했어요. 확인해주세요!`,
      sound: "default",
      data: { kind: REMINDER_KIND, noticeId, scheduledAt: date.getTime() },
    },
    trigger: {
      type: api.SchedulableTriggerInputTypes.DATE,
      date,
      channelId: CHANNEL,
    },
  });
}

export async function cancelReminder(noticeId: string): Promise<void> {
  const api = await notifications();
  await api.cancelScheduledNotificationAsync(reminderIdentifier(noticeId));
}
