import { useEffect } from "react";
import Constants from "expo-constants";
import { AppState, Platform } from "react-native";
import { router } from "expo-router";
import * as Notifications from "expo-notifications";
import { notificationNoticeId } from "./keywords";
import { refreshPushRegistration } from "./api";
import { authStorage } from "./authStorage";

// The common notice channel is also available to deadline notifications (#86).
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});
export function useKeywordNotifications(ready: boolean) {
  useEffect(() => {
    if (!ready || Platform.OS !== "android" || Constants.executionEnvironment === "storeClient") return;
    let active = true;
    let handling = false;
    async function open(response: Notifications.NotificationResponse | null) {
      if (!response || handling || !active) return;
      const id = notificationNoticeId(
        response.notification.request.content.data ?? {},
      );
      if (!id) return;
      handling = true;
      try {
        const identifier = response.notification.request.identifier;
        if (
          (await authStorage.getItem("keyword-last-opened")) === identifier ||
          !active
        )
          return;
        await authStorage.setItem("keyword-last-opened", identifier);
        if (active) router.push({ pathname: "/notice/[id]", params: { id } });
      } finally {
        handling = false;
      }
    }
    const receive = (response: Notifications.NotificationResponse | null) => {
      void open(response).catch(() => {});
    };
    const subscription =
      Notifications.addNotificationResponseReceivedListener(receive);
    void Notifications.getLastNotificationResponseAsync()
      .then(receive)
      .catch(() => {});
    const refresh = () => {
      void refreshPushRegistration().catch(() => {});
    };
    refresh();
    const state = AppState.addEventListener("change", (value) => {
      if (value === "active") refresh();
    });
    const tokens = Notifications.addPushTokenListener(refresh);
    return () => {
      active = false;
      subscription.remove();
      state.remove();
      tokens.remove();
    };
  }, [ready]);
}
