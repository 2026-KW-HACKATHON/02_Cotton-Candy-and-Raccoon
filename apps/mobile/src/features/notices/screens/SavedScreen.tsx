import { useState } from "react";
import { Pressable, View, useWindowDimensions } from "react-native";
import { FloatingSettingsButton } from "@/shared/ui/FloatingSettingsButton";
import { Header } from "@/shared/ui/Header";
import { Screen } from "@/shared/ui/Screen";
import { AppText } from "@/shared/ui/AppText";
import { COLORS } from "@/shared/theme/tokens";
import { NoticeCard } from "../components/NoticeCard";
import { NoticeState } from "../components/NoticeState";
import { useNotices } from "../hooks/useNotices";
import { useBookmarkStore } from "../store/bookmarkStore";
import { useDisplayPreferences } from "@/shared/accessibility/displayPreferences";
import { EasyNoticeListScreen } from "./EasyNoticeListScreen";

export function SavedScreen() {
  const mode = useDisplayPreferences((state) => state.mode);
  return mode === "easy" ? (
    <EasyNoticeListScreen kind="saved" />
  ) : (
    <StandardSavedScreen />
  );
}
function StandardSavedScreen() {
  const query = useNotices();
  const { width } = useWindowDimensions();
  const fontScale = useDisplayPreferences((state) => state.fontScale);
  // 좁은 화면과 큰 글자에서는 날짜·제공처와 북마크가 겹치지 않도록 한 열을 쓴다.
  const singleColumn =
    (Math.min(width, 600) - 31.384) * 0.483 < 150 * fontScale;
  const ids = useBookmarkStore((state) => state.savedIds);
  const [recentFirst, setRecentFirst] = useState(true);
  // savedIds의 추가 순서를 보관 시점으로 사용한다. 공문 게시일 정렬과는 별개다.
  const saved = (query.data ?? [])
    .filter((notice) => ids.includes(notice.id))
    .sort((a, b) =>
      recentFirst
        ? ids.indexOf(b.id) - ids.indexOf(a.id)
        : ids.indexOf(a.id) - ids.indexOf(b.id),
    );
  return (
    <Screen
      headerBehavior="reveal"
      overlay={<FloatingSettingsButton />}
      header={
        <Header
          title="다시 볼 소식"
          right={<View style={{ width: 44.308 }} />}
        />
      }
      floating
      contentStyle={{ paddingHorizontal: 15.692 }}
    >
      <AppText secondary>관심 있는 공문을 모아뒀어요.</AppText>
      <View
        style={{
          flexDirection: "row",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <AppText variant="medium">보관한 공문 {saved.length}건</AppText>
        <Pressable
          accessibilityRole="button"
          onPress={() => setRecentFirst((value) => !value)}
          style={{ minHeight: 44, justifyContent: "center" }}
        >
          <AppText size={12.923} style={{ color: COLORS.primary }}>
            {recentFirst ? "최근 보관순 ↓" : "먼저 보관순 ↑"}
          </AppText>
        </Pressable>
      </View>
      {query.isPending || query.isError ? (
        <NoticeState
          loading={query.isPending}
          error={query.isError}
          retry={() => {
            void query.refetch();
          }}
        />
      ) : !saved.length ? (
        <NoticeState message="아직 보관한 공문이 없어요. 공문의 북마크를 눌러 모아보세요." />
      ) : (
        <View
          style={{
            flexDirection: "row",
            flexWrap: "wrap",
            justifyContent: "space-between",
            rowGap: 14.769,
          }}
        >
          {saved.map((notice) => (
            <NoticeCard
              key={notice.id}
              notice={notice}
              grid
              fullWidth={singleColumn}
            />
          ))}
        </View>
      )}
      <AppText secondary size={11.08}>
        예시 데이터의 보관 상태는 앱 실행 중에만 유지됩니다.
      </AppText>
    </Screen>
  );
}
