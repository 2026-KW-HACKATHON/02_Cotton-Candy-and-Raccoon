import { Pressable, StyleSheet, View } from "react-native";
import { router } from "expo-router";
import { ChevronRight } from "lucide-react-native";
import { Header } from "@/shared/ui/Header";
import { Screen } from "@/shared/ui/Screen";
import { AppText } from "@/shared/ui/AppText";
import { COLORS } from "@/shared/theme/tokens";
import { LetterOrbit } from "../components/LetterOrbit";
import { NoticeState } from "../components/NoticeState";
import { ScopeDropdown } from "../components/ScopeDropdown";
import { useNotices } from "../hooks/useNotices";
import { useDisplayPreferences } from "@/shared/accessibility/displayPreferences";
import { EasyNoticeListScreen } from "./EasyNoticeListScreen";

export function HomeScreen() {
  const mode = useDisplayPreferences((state) => state.mode);
  return mode === "easy" ? (
    <EasyNoticeListScreen kind="home" />
  ) : (
    <StandardHomeScreen />
  );
}
function StandardHomeScreen() {
  const query = useNotices();
  const notices = query.data ?? [];
  const today = new Date();
  const date = `${today.getFullYear()}. ${String(today.getMonth() + 1).padStart(2, "0")}. ${String(today.getDate()).padStart(2, "0")}`;
  return (
    <Screen
      header={<Header title="월계알리미" variant="home" />}
      floating
      contentStyle={styles.content}
    >
      <View style={styles.greeting}>
        <View
          accessible
          accessibilityLabel="오늘의 공문이 도착했어요"
          style={styles.greetingTitle}
        >
          {["오늘의", "공문이", "도착했어요"].map((word, wordIndex) => (
            <AppText key={word} variant="display" size={32} lineHeight={44}>
              {word}
              {wordIndex < 2 ? " " : ""}
            </AppText>
          ))}
        </View>
        <View style={styles.greetingMeta}>
          <AppText
            secondary
            size={12.923}
            lineHeight={20.308}
            style={{ flex: 1 }}
          >
            최근 공문 · {date}
          </AppText>
          <ScopeDropdown />
        </View>
      </View>
      {query.isError && query.data && (
        <NoticeState
          error
          errorDetail={query.error}
          retrying={query.isFetching}
          retry={() => {
            void query.refetch();
          }}
        />
      )}
      {query.isPending || (query.isError && !query.data) ? (
        <NoticeState
          loading={query.isPending}
          error={query.isError}
          errorDetail={query.error}
          retrying={query.isFetching}
          retry={() => {
            void query.refetch();
          }}
        />
      ) : !notices.length ? (
        <NoticeState message="도착한 공문이 없어요." />
      ) : (
        <LetterOrbit
          notices={notices}
          onOpen={(notice) =>
            router.push({ pathname: "/notice/[id]", params: { id: notice.id } })
          }
        />
      )}
      <Pressable
        accessibilityRole="button"
        onPress={() => router.navigate("/notices")}
        style={({ pressed }) => [styles.all, pressed && { opacity: 0.75 }]}
      >
        <AppText
          variant="medium"
          size={16}
          lineHeight={24}
          style={{ color: COLORS.surface }}
        >
          전체 공문 보기
        </AppText>
        <ChevronRight size={20} color={COLORS.surface} />
      </Pressable>
      <View style={styles.service}>
        <AppText secondary size={11} lineHeight={17} style={styles.centered}>
          월계알리미는 노원구청의 공식 서비스가 아닙니다.
        </AppText>
        <AppText secondary size={11} lineHeight={17} style={styles.centered}>
          공개된 공지 정보를 모아 쉽게 전달해요.
        </AppText>
      </View>
    </Screen>
  );
}
const styles = StyleSheet.create({
  content: { paddingHorizontal: 0, paddingTop: 8, gap: 0 },
  greeting: { paddingHorizontal: 18.462, gap: 3.692 },
  greetingTitle: { flexDirection: "row", flexWrap: "wrap" },
  greetingMeta: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8,
  },
  all: {
    marginTop: 8,
    marginHorizontal: 24,
    backgroundColor: "#276197",
    borderRadius: 16,
    flexDirection: "row",
    gap: 4,
    alignItems: "center",
    justifyContent: "center",
    minHeight: 48,
  },
  service: { paddingHorizontal: 20, paddingTop: 14, paddingBottom: 4, gap: 2 },
  centered: { textAlign: "center" },
});
