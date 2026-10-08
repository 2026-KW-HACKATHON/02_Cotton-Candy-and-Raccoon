import { useEffect } from "react";
import { Platform } from "react-native";
import { useRouter, useRootNavigationState } from "expo-router";
import { reminderNoticeId } from "./reminderDate";

/** Mount after onboarding; keep the cold-start response until navigation is ready. */
export function NotificationNavigation() {
  const router = useRouter();
  const navigation = useRootNavigationState();
  useEffect(() => {
    if (Platform.OS !== "android" || !navigation?.key) return;
    let active = true;
    let cleanup: (() => void) | undefined;
    const handled = new Set<string>();
    void import("expo-notifications")
      .then(async (api) => {
        if (!active) return;
        api.setNotificationHandler({
          handleNotification: async () => ({
            shouldShowBanner: true,
            shouldShowList: true,
            shouldPlaySound: true,
            shouldSetBadge: false,
          }),
        });
        const receive = (
          response: import("expo-notifications").NotificationResponse | null,
        ) => {
          if (
            !active ||
            !response ||
            response.actionIdentifier !== api.DEFAULT_ACTION_IDENTIFIER
          )
            return;
          const request = response.notification.request;
          const id = reminderNoticeId(request.content.data);
          const key = `${request.identifier}:${response.notification.date}`;
          if (!id || handled.has(key)) return;
          handled.add(key);
          router.push({ pathname: "/notice/[id]", params: { id } });
          void api.clearLastNotificationResponseAsync().catch(() => undefined);
        };
        const subscription =
          api.addNotificationResponseReceivedListener(receive);
        cleanup = () => subscription.remove();
        receive(await api.getLastNotificationResponseAsync());
      })
      .catch(() => undefined);
    return () => {
      active = false;
      cleanup?.();
    };
  }, [navigation?.key, router]);
  return null;
}
