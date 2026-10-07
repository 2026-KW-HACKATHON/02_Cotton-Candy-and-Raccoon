import { Pressable, StyleSheet, View } from "react-native";
import { Image } from "expo-image";
import { Bookmark } from "lucide-react-native";
import { router } from "expo-router";
import { AppText } from "@/shared/ui/AppText";
import { COLORS, CARD_SHADOW, RADIUS, SPACE } from "@/shared/theme/tokens";
import { CategoryBadge } from "./CategoryBadge";
import { type Notice } from "../types/notice";
import { useBookmarkStore } from "../store/bookmarkStore";

const STRIPES = [
  require("@/assets/figma/list-imgLeftStripe1.svg"),
  require("@/assets/figma/list-imgLeftStripe2.svg"),
];
const BOTTOM_STRIPES = [
  require("@/assets/figma/saved-imgBottomStripe1.svg"),
  require("@/assets/figma/saved-imgBottomStripe2.svg"),
];
export function NoticeCard({
  notice,
  grid = false,
}: {
  notice: Notice;
  grid?: boolean;
}) {
  const saved = useBookmarkStore((state) => state.savedIds.includes(notice.id));
  const toggleBookmark = useBookmarkStore((state) => state.toggleBookmark);
  return (
    <View style={[styles.card, grid && styles.grid]}>
      <View pointerEvents="none" style={StyleSheet.absoluteFill}>
        {Array.from({ length: 9 }, (_, index) => (
          <Image
            key={index}
            source={(grid ? BOTTOM_STRIPES : STRIPES)[index % 2]}
            style={
              grid
                ? {
                    position: "absolute",
                    bottom: 5.54,
                    left: `${9.3 * (index + 1)}%`,
                    width: 11.093,
                    height: 3.69231,
                  }
                : {
                    position: "absolute",
                    top: 13.85 + index * 18.462,
                    left: 6.46,
                    width: 5.53846,
                    height: 15.3107,
                  }
            }
          />
        ))}
      </View>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${notice.title}, 상세 보기`}
        onPress={() =>
          router.push({ pathname: "/notice/[id]", params: { id: notice.id } })
        }
        style={[styles.content, grid && styles.gridContent]}
      >
        <View style={{ paddingRight: grid ? 0 : 75, gap: SPACE.md }}>
          <CategoryBadge>{notice.category}</CategoryBadge>
          <AppText variant="bold" size={grid ? 14.77 : 16.62}>
            {notice.title}
          </AppText>
        </View>
        {!grid && (
          <AppText secondary size={12.92}>
            {notice.description}
          </AppText>
        )}
        <View style={[styles.footer, grid && styles.gridFooter]}>
          <AppText
            secondary
            size={grid ? 11.08 : 12.92}
            style={!grid && { flex: 1 }}
          >
            {notice.provider}
          </AppText>
          <AppText secondary size={grid ? 11.08 : 12.92}>
            {notice.publishedAt}
          </AppText>
        </View>
      </Pressable>
      {!grid && (
        <Image
          pointerEvents="none"
          source={require("@/assets/figma/list-imgPostageStampPerforatedBorder.svg")}
          style={styles.stamp}
        />
      )}
      {saved && (
        <Image
          pointerEvents="none"
          source={require("@/assets/figma/postmark.png")}
          style={grid ? styles.gridPostmark : styles.postmark}
          contentFit="contain"
        />
      )}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={
          saved ? `${notice.title} 보관 해제` : `${notice.title} 보관하기`
        }
        accessibilityState={{ selected: saved }}
        aria-pressed={saved}
        onPress={() => toggleBookmark(notice.id)}
        style={[
          styles.bookmark,
          grid ? styles.gridBookmark : styles.listBookmark,
        ]}
      >
        <Bookmark
          size={22}
          strokeWidth={1.5}
          color={saved ? COLORS.primary : COLORS.secondary}
          fill={saved ? COLORS.primary : "none"}
        />
      </Pressable>
    </View>
  );
}
const styles = StyleSheet.create({
  card: {
    backgroundColor: COLORS.surface,
    borderColor: COLORS.border,
    borderWidth: 0.923,
    borderRadius: RADIUS.card,
    minHeight: 190.154,
    ...CARD_SHADOW,
  },
  content: {
    paddingLeft: 29.538,
    paddingRight: SPACE.xl,
    paddingVertical: SPACE.xl,
    gap: SPACE.md,
  },
  grid: { width: "48.3%", minHeight: 212.308 },
  gridContent: { padding: SPACE.xl, paddingBottom: 59, gap: SPACE.md },
  footer: { flexDirection: "row", alignItems: "center", gap: 8 },
  gridFooter: { flexDirection: "column", alignItems: "flex-start", gap: 0 },
  stamp: {
    position: "absolute",
    top: SPACE.xl,
    right: SPACE.xl,
    width: 36.9231,
    height: 44.3077,
  },
  bookmark: {
    position: "absolute",
    width: 44,
    height: 44,
    alignItems: "center",
    justifyContent: "center",
  },
  gridBookmark: { bottom: 14.77, right: 14.77 },
  listBookmark: { top: SPACE.xl, right: 15 },
  postmark: {
    position: "absolute",
    top: 36,
    right: 25,
    width: 59.077,
    height: 44.308,
  },
  gridPostmark: {
    position: "absolute",
    top: 12.923,
    right: 14.77,
    width: 51.692,
    height: 38.769,
  },
});
