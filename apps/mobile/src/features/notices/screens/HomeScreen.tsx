import { NoticeQueryFeedback } from "../components/NoticeReadStatus";
import { useEffect, useRef, useState } from "react";
import {
  FlatList,
  Platform,
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
  LETTER_WIDTH,
  LETTER_HEIGHT,
} from "../components/LetterIllustration";
import { NoticeState } from "../components/NoticeState";
import { ScopeDropdown } from "../components/ScopeDropdown";
import { useNoticeScopeStore } from "../store/noticeScopeStore";
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
  const source = useNoticeScopeStore((state) => state.source);
  const query = useNotices({ source });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const list = useRef<FlatList<Notice>>(null);
  const scrollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const width = Math.min(useWindowDimensions().width, 600);
  const pageWidth = Math.min(328.615, width);
  const scale = Math.min(1, (width - 40) / LETTER_WIDTH);
  const notices = query.data ?? [];
  const selectedIndex = notices.findIndex((notice) => notice.id === selectedId);
  const index = Math.max(0, selectedIndex);
  const currentId = notices[index]?.id;
  useEffect(() => {
    list.current?.scrollToOffset({
      offset: index * pageWidth,
      animated: false,
    });
  }, [index, currentId, pageWidth]);
  useEffect(
    () => () => {
      if (scrollTimer.current !== null) clearTimeout(scrollTimer.current);
    },
    [pageWidth, query.data],
  );
  function move(next: number) {
    const target = Math.max(0, Math.min(next, notices.length - 1));
    list.current?.scrollToOffset({
      offset: target * pageWidth,
      animated: true,
    });
    setSelectedId(notices[target]?.id ?? null);
  }
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
            등록일 최신순
          </AppText>
          <ScopeDropdown />
        </View>
      </View>
      <NoticeQueryFeedback
        error={query.error}
        hasData={query.data !== undefined}
        retry={() => void query.refetch()}
      />
      {query.isPending ? (
        <NoticeState
          loading={query.isPending}
          error={query.isError}
          retry={() => {
            void query.refetch();
          }}
        />
      ) : query.isError && query.data === undefined ? null : !notices.length ? (
        <NoticeState message="등록된 공문이 없어요." />
      ) : (
        <>
          <FlatList
            ref={list}
            horizontal
            data={notices}
            extraData={index}
            CellRendererComponent={({
              children,
              index: cellIndex,
              style,
              onLayout,
            }) => (
              <View
                onLayout={onLayout}
                style={[style, { zIndex: cellIndex === index ? 1 : 0 }]}
              >
                {children}
              </View>
            )}
            key={pageWidth}
            initialScrollIndex={index}
            keyExtractor={(notice) => notice.id}
            showsHorizontalScrollIndicator={false}
            snapToInterval={pageWidth}
            decelerationRate="fast"
            removeClippedSubviews={false}
            scrollEventThrottle={16}
            onScroll={(event) => {
              // 웹 휠 스크롤에는 momentum 종료 이벤트가 없어 멈춘 위치를 직접 맞춘다.
              if (Platform.OS !== "web") return;
              const next = Math.round(
                event.nativeEvent.contentOffset.x / pageWidth,
              );
              if (scrollTimer.current !== null)
                clearTimeout(scrollTimer.current);
              scrollTimer.current = setTimeout(() => move(next), 120);
            }}
            // 회전한 캐릭터 레이어까지 스크롤 영역 안에 들어오도록 상단 여백을 확보한다.
            contentContainerStyle={{
              paddingHorizontal: (width - pageWidth) / 2,
              paddingTop: 12,
              paddingBottom: 92,
            }}
            onMomentumScrollEnd={(event) =>
              setSelectedId(
                notices[
                  Math.max(
                    0,
                    Math.min(
                      notices.length - 1,
                      Math.round(event.nativeEvent.contentOffset.x / pageWidth),
                    ),
                  )
                ]?.id ?? null,
              )
            }
            getItemLayout={(_, itemIndex) => ({
              length: pageWidth,
              offset: pageWidth * itemIndex,
              index: itemIndex,
            })}
            renderItem={({ item, index: itemIndex }) => {
              const selected = itemIndex === index;
              const ItemContainer = selected ? View : Pressable;
              const direction = itemIndex < index ? -1 : 1;
              // 중앙 편지 기준 Figma의 원호 배치를 적용하되 페이지 간격은 그대로 둔다.
              const envelopeOffset =
                direction < 0
                  ? pageWidth - 202.595 * scale
                  : 217.354 * scale - pageWidth;
              const open = () =>
                router.push({
                  pathname: "/notice/[id]",
                  params: { id: item.id },
                });
              return (
                <ItemContainer
                  accessible={!selected}
                  tabIndex={selected ? undefined : 0}
                  accessibilityRole={selected ? undefined : "button"}
                  accessibilityLabel={
                    selected
                      ? undefined
                      : itemIndex < index
                        ? "이전 공문 봉투"
                        : "다음 공문 봉투"
                  }
                  // 중앙 편지의 상세 이동과 접근성 포커스는 내부 버튼이 담당한다.
                  onPress={selected ? undefined : () => move(index + direction)}
                  style={{
                    width: pageWidth,
                    height: LETTER_HEIGHT * scale,
                    alignItems: "center",
                    justifyContent: "flex-end",
                    zIndex: selected ? 1 : 0,
                    transform: selected
                      ? undefined
                      : [{ translateX: envelopeOffset }],
                  }}
                >
                  <View
                    style={{
                      width: selected ? LETTER_WIDTH : 289.52,
                      height: selected ? LETTER_HEIGHT : 170.306,
                      opacity: selected ? 1 : 0.5,
                      transform: selected
                        ? [{ scale }]
                        : [
                            { translateY: 50.882 * scale },
                            { scale: scale * (287.969 / 289.52) },
                            { rotate: `${direction < 0 ? -12 : 11.99}deg` },
                          ],
                      transformOrigin: selected ? "bottom center" : "center",
                    }}
                  >
                    {selected ? (
                      <LetterIllustration notice={item} onOpen={open} />
                    ) : (
                      <ClosedEnvelope />
                    )}
                  </View>
                </ItemContainer>
              );
            }}
          />
          <View style={styles.controls}>
            <IconButton
              accessibilityLabel="이전 공문"
              accessibilityState={{ disabled: index === 0 }}
              disabled={index === 0}
              onPress={() => move(index - 1)}
              style={styles.arrow}
            >
              <ChevronLeft size={20} color={COLORS.primary} />
            </IconButton>
            <AppText
              variant="medium"
              secondary
              size={16}
              lineHeight={24}
              accessibilityLiveRegion="polite"
              style={{ width: 64, textAlign: "center" }}
            >
              {index + 1} / {notices.length}
            </AppText>
            <IconButton
              accessibilityLabel="다음 공문"
              accessibilityState={{
                disabled: index === notices.length - 1,
              }}
              disabled={index === notices.length - 1}
              onPress={() => move(index + 1)}
              style={styles.arrow}
            >
              <ChevronRight size={20} color={COLORS.primary} />
            </IconButton>
          </View>
          {query.hasNextPage && (
            <Pressable
              accessibilityRole="button"
              disabled={query.isFetchingNextPage}
              onPress={() => void query.fetchNextPage()}
              style={{
                minHeight: 48,
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <AppText>
                {query.isFetchingNextPage ? "불러오는 중" : "더 보기"}
              </AppText>
            </Pressable>
          )}
        </>
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
  controls: {
    marginTop: -74,
    flexDirection: "row",
    gap: 20,
    alignItems: "center",
    justifyContent: "center",
  },
  arrow: { minWidth: 48, minHeight: 48 },
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
