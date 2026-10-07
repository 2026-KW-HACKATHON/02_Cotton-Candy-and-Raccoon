import { useRef, useState } from "react";
import {
  FlatList,
  Pressable,
  StyleSheet,
  View,
  useWindowDimensions,
} from "react-native";
import { router } from "expo-router";
import { ChevronLeft, ChevronRight } from "lucide-react-native";
import { Header } from "@/shared/ui/Header";
import { Screen } from "@/shared/ui/Screen";
import { AppText } from "@/shared/ui/AppText";
import { IconButton } from "@/shared/ui/IconButton";
import { COLORS } from "@/shared/theme/tokens";
import {
  LetterIllustration,
  ClosedEnvelope,
} from "../components/LetterIllustration";
import { NoticeState } from "../components/NoticeState";
import { useNotices } from "../hooks/useNotices";
import { type Notice } from "../types/notice";
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
  const [index, setIndex] = useState(0);
  const list = useRef<FlatList<Notice>>(null);
  // 공통 Screen의 최대 너비에 맞춰 페이지 간격을 계산하고, 좁은 화면에서는 그림만 축소한다.
  const width = Math.min(useWindowDimensions().width, 600);
  const pageWidth = Math.min(328.615, width);
  const scale = Math.min(1, (width - 24) / 313.846);
  const notices = query.data ?? [];
  function move(next: number) {
    const target = Math.max(0, Math.min(next, notices.length - 1));
    list.current?.scrollToOffset({
      offset: target * pageWidth,
      animated: true,
    });
    setIndex(target);
  }
  return (
    <Screen
      header={<Header title="월계알리미" />}
      floating
      contentStyle={{ paddingHorizontal: 0, gap: 7.385 }}
    >
      <View style={styles.greeting}>
        <AppText variant="display" size={32} style={{ lineHeight: 44 }}>
          이번 주 소식이{"\n"}도착했어요
        </AppText>
        <AppText secondary size={12.923}>
          이번 주 공문 · 예시 09.28 — 10.04
        </AppText>
      </View>
      {query.isPending || query.isError ? (
        <NoticeState
          loading={query.isPending}
          error={query.isError}
          retry={() => {
            void query.refetch();
          }}
        />
      ) : !notices.length ? (
        <NoticeState message="이번 주에 도착한 공문이 없어요." />
      ) : (
        <>
          <FlatList
            ref={list}
            horizontal
            data={notices}
            extraData={index}
            keyExtractor={(notice) => notice.id}
            showsHorizontalScrollIndicator={false}
            snapToInterval={pageWidth}
            decelerationRate="fast"
            contentContainerStyle={{
              paddingHorizontal: (width - pageWidth) / 2,
            }}
            onMomentumScrollEnd={(event) =>
              setIndex(
                Math.round(event.nativeEvent.contentOffset.x / pageWidth),
              )
            }
            getItemLayout={(_, itemIndex) => ({
              length: pageWidth,
              offset: pageWidth * itemIndex,
              index: itemIndex,
            })}
            renderItem={({ item, index: itemIndex }) => (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={
                  itemIndex === index
                    ? `${item.title}, 상세 보기`
                    : itemIndex < index
                      ? "이전 공문"
                      : "다음 공문"
                }
                onPress={() => {
                  // 닫힌 봉투는 방향 버튼과 같은 이동을 하고, 열린 편지만 상세를 연다.
                  if (itemIndex !== index) {
                    move(index + (itemIndex < index ? -1 : 1));
                    return;
                  }
                  router.push({
                    pathname: "/notice/[id]",
                    params: { id: item.id },
                  });
                }}
                style={{
                  width: pageWidth,
                  height: 470.769 * scale,
                  alignItems: "center",
                  justifyContent: "flex-end",
                }}
              >
                <View
                  style={{
                    width: 313.846,
                    height: itemIndex === index ? 470.769 : 184.615,
                    transform: [{ scale }],
                    transformOrigin: "bottom center",
                  }}
                >
                  {itemIndex === index ? (
                    <LetterIllustration notice={item} />
                  ) : (
                    <ClosedEnvelope />
                  )}
                </View>
              </Pressable>
            )}
          />
          <View style={styles.controls}>
            <IconButton
              accessibilityLabel="이전 공문"
              accessibilityState={{ disabled: index === 0 }}
              disabled={index === 0}
              onPress={() => move(index - 1)}
            >
              <ChevronLeft size={20} color={COLORS.secondary} />
            </IconButton>
            <AppText secondary style={{ width: 59.077, textAlign: "center" }}>
              {index + 1} / {notices.length}
            </AppText>
            <IconButton
              accessibilityLabel="다음 공문"
              accessibilityState={{ disabled: index === notices.length - 1 }}
              disabled={index === notices.length - 1}
              onPress={() => move(index + 1)}
            >
              <ChevronRight size={20} color={COLORS.secondary} />
            </IconButton>
          </View>
        </>
      )}
      <Pressable
        accessibilityRole="button"
        onPress={() => router.navigate("/notices")}
        style={styles.all}
      >
        <AppText style={{ color: COLORS.primary }}>전체 공문 보기</AppText>
        <ChevronRight size={20} color={COLORS.primary} />
      </Pressable>
    </Screen>
  );
}
const styles = StyleSheet.create({
  greeting: { paddingHorizontal: 18.462, gap: 3.692 },
  controls: {
    flexDirection: "row",
    gap: 18.462,
    alignItems: "center",
    justifyContent: "center",
  },
  all: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    minHeight: 44,
  },
});
