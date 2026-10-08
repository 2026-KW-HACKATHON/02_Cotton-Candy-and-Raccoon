import { Platform } from "react-native";
import Constants from "expo-constants";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";

export async function pushToken(requestPermission = true): Promise<string> {
  if (
    Platform.OS !== "android" ||
    !Device.isDevice ||
    Constants.executionEnvironment === "storeClient"
  )
    throw new Error("알림은 설치한 Android 앱에서 사용할 수 있어요.");
  await Notifications.setNotificationChannelAsync("notice-alerts", {
    name: "공지 알림",
    importance: Notifications.AndroidImportance.DEFAULT,
  });
  let permission = await Notifications.getPermissionsAsync();
  if (!permission.granted && requestPermission && permission.canAskAgain)
    permission = await Notifications.requestPermissionsAsync();
  if (!permission.granted)
    throw new Error("휴대전화 설정에서 알림을 허용해 주세요.");
  const projectId =
    Constants.easConfig?.projectId ??
    Constants.expoConfig?.extra?.eas?.projectId;
  if (!projectId) throw new Error("알림 서비스 연결을 준비 중이에요.");
  return (await Notifications.getExpoPushTokenAsync({ projectId })).data;
}
