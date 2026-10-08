import {
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from "react-native";
import { Search } from "lucide-react-native";
import { Header } from "@/shared/ui/Header";
import { Screen } from "@/shared/ui/Screen";
import { AppText } from "@/shared/ui/AppText";
import { COLORS, FONTS, RADIUS, SPACE } from "@/shared/theme/tokens";
import { useDisplayPreferences } from "@/shared/accessibility/displayPreferences";
import { NoticeCard } from "../components/NoticeCard";
import { NoticeState } from "../components/NoticeState";
import { NoticeActionButton } from "../components/NoticeActionButton";
import { ScopeDropdown } from "../components/ScopeDropdown";
import {
  CATEGORIES,
  useNoticeListViewModel,
} from "../hooks/useNoticeListViewModel";
import { EasyNoticeListScreen } from "./EasyNoticeListScreen";

export function NoticeListScreen() {
  const mode = useDisplayPreferences((state) => state.mode);
  return mode === "easy" ? (
    <EasyNoticeListScreen kind="notices" />
  ) : (
    <StandardNoticeListScreen />
  );
}
function StandardNoticeListScreen() {
  const vm = useNoticeListViewModel();
  const fontScale = useDisplayPreferences((state) => state.fontScale);
  return (
    <Screen
      headerBehavior="reveal"
      header={
        <View>
          <Header
            title="우리 동네 공문"
            variant="home"
            right={<ScopeDropdown />}
          />
          <View style={styles.filters}>
            <AppText secondary size={16} lineHeight={26}>
              필요한 소식을 한곳에서 찾아보세요.
            </AppText>
            <View style={styles.search}>
              <Search color={COLORS.secondary} size={20} strokeWidth={1.5} />
              <TextInput
                accessibilityLabel="공문 제목이나 내용 검색"
                placeholder="공문 제목이나 내용을 검색해요"
                placeholderTextColor={COLORS.secondary}
                value={vm.search}
                onChangeText={vm.setSearch}
                returnKeyType="search"
                style={[styles.input, { fontSize: 12.92 * fontScale }]}
              />
            </View>
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={{ gap: 7.385 }}
            >
              {CATEGORIES.map((category) => (
                <Pressable
                  key={category}
                  accessibilityRole="button"
                  accessibilityState={{ selected: vm.category === category }}
                  aria-pressed={vm.category === category}
                  onPress={() => vm.setCategory(category)}
                  style={[
                    styles.chip,
                    vm.category === category && styles.selected,
                  ]}
                >
                  <AppText
                    size={12.92}
                    style={{
                      color:
                        vm.category === category
                          ? COLORS.surface
                          : COLORS.secondary,
                    }}
                  >
                    {category}
                  </AppText>
                </Pressable>
              ))}
            </ScrollView>
          </View>
        </View>
      }
      floating
    >
      <View style={styles.count}>
        <AppText secondary size={12.923}>
          불러온 공문 {vm.notices.length}건
        </AppText>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={
            vm.newestFirst
              ? "최신순, 누르면 오래된순으로 변경"
              : "오래된순, 누르면 최신순으로 변경"
          }
          onPress={vm.toggleSort}
          style={{ minHeight: 44, justifyContent: "center" }}
        >
          <AppText size={12.923} style={{ color: COLORS.primary }}>
            {vm.newestFirst ? "최신순 ↓" : "오래된순 ↑"}
          </AppText>
        </Pressable>
      </View>
      {vm.isError && vm.data && (
        <NoticeState
          error
          errorDetail={vm.error}
          retrying={vm.isFetching}
          retry={() => {
            void vm.refetch();
          }}
        />
      )}
      {vm.isPending || (vm.isError && !vm.data) ? (
        <NoticeState
          loading={vm.isPending}
          error={vm.isError}
          errorDetail={vm.error}
          retrying={vm.isFetching}
          retry={() => {
            void vm.refetch();
          }}
        />
      ) : vm.notices.length ? (
        vm.notices.map((notice) => (
          <NoticeCard key={notice.id} notice={notice} />
        ))
      ) : (
        <NoticeState message="검색 조건에 맞는 공문이 없어요." />
      )}
      {vm.hasNextPage && (
        <NoticeActionButton
          label={vm.isFetchingNextPage ? "불러오는 중…" : "공문 더 보기"}
          disabled={vm.isFetchingNextPage}
          onPress={() => {
            void vm.fetchNextPage();
          }}
        />
      )}
    </Screen>
  );
}
const styles = StyleSheet.create({
  filters: {
    paddingHorizontal: 20,
    paddingTop: 8,
    paddingBottom: SPACE.lg,
    gap: SPACE.lg,
  },
  search: {
    minHeight: 48,
    backgroundColor: COLORS.soft,
    borderRadius: RADIUS.control,
    paddingHorizontal: 14.769,
    flexDirection: "row",
    gap: 11.077,
    alignItems: "center",
  },
  input: {
    flex: 1,
    minHeight: 48,
    color: COLORS.text,
    fontFamily: FONTS.body,
    paddingVertical: 8,
  },
  chip: {
    minHeight: 44.308,
    minWidth: 70.154,
    paddingHorizontal: 17,
    justifyContent: "center",
    alignItems: "center",
    borderRadius: 999,
    borderWidth: 0.923,
    borderColor: COLORS.border,
  },
  selected: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  count: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginVertical: -11,
  },
});
