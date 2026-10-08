import { useEffect, useState } from "react";
import {
  AccessibilityInfo,
  Animated,
  Easing,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  View,
  useWindowDimensions,
} from "react-native";
import { Image } from "expo-image";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { AppText } from "@/shared/ui/AppText";
import { COLORS, CARD_SHADOW } from "@/shared/theme/tokens";
import { type GlossaryTerm } from "../types/notice";

// Figma 440:1333/1363/1393/1348/1378/1408, 조회 2026-10-08.
export function NoticeTermOverlay({
  term,
  easy,
  onClose,
  comfortable = false,
}: {
  term: GlossaryTerm | null;
  easy: boolean;
  onClose: () => void;
  comfortable?: boolean;
}) {
  const insets = useSafeAreaInsets();
  const { height } = useWindowDimensions();
  const bottomPadding = Math.max(comfortable ? 24 : 54, insets.bottom + 16);
  const [offset] = useState(() => new Animated.Value(0));
  const [reduceMotion, setReduceMotion] = useState(true);
  useEffect(() => {
    let mounted = true;
    void AccessibilityInfo.isReduceMotionEnabled().then((value) => {
      if (mounted) setReduceMotion(value);
    });
    const subscription = AccessibilityInfo.addEventListener(
      "reduceMotionChanged",
      setReduceMotion,
    );
    return () => {
      mounted = false;
      subscription.remove();
    };
  }, []);
  useEffect(() => {
    offset.setValue(term && !reduceMotion ? height : 0);
    const animation = Animated.timing(offset, {
      toValue: 0,
      duration: reduceMotion ? 0 : 200,
      easing: Easing.out(Easing.ease),
      useNativeDriver: true,
    });
    if (term) animation.start();
    return () => animation.stop();
  }, [term, height, reduceMotion, offset]);
  return (
    <Modal
      visible={term !== null}
      transparent
      animationType="none"
      onRequestClose={onClose}
    >
      <View
        style={[
          styles.overlay,
          {
            paddingTop: insets.top + 16,
            paddingBottom: bottomPadding,
          },
        ]}
      >
        <Pressable
          style={StyleSheet.absoluteFill}
          accessibilityRole="button"
          accessibilityLabel="단어 설명 닫기"
          onPress={onClose}
        />
        <Animated.View
          accessibilityViewIsModal
          style={[
            styles.sheet,
            {
              maxHeight: height - insets.top - bottomPadding - 16,
              transform: [{ translateY: offset }],
            },
          ]}
        >
          <ScrollView
            contentContainerStyle={[
              styles.content,
              comfortable && { paddingHorizontal: 20 },
            ]}
            bounces={false}
          >
            <View style={styles.handle} />
            <View style={styles.header}>
              <AppText
                variant="bold"
                size={comfortable ? 20 : 16}
                lineHeight={comfortable ? 30 : 26}
                style={{ flex: 1 }}
              >
                {easy ? "원문 단어" : "단어 뜻"}
              </AppText>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="닫기"
                onPress={onClose}
                style={comfortable && styles.comfortableClose}
              >
                {comfortable ? (
                  <AppText
                    variant="bold"
                    size={20}
                    lineHeight={30}
                    style={{ color: COLORS.primary }}
                  >
                    닫기
                  </AppText>
                ) : (
                  <Image
                    source={require("@/assets/figma/detail-overlay/close.svg")}
                    style={{ width: 44, height: 44 }}
                  />
                )}
              </Pressable>
            </View>
            <AppText
              variant="display"
              size={24}
              lineHeight={comfortable ? 36 : 34}
              style={{ color: COLORS.primary }}
            >
              {term?.original}
            </AppText>
            {!easy && (
              <AppText
                size={comfortable ? 20 : 16}
                lineHeight={comfortable ? 30 : 26}
              >
                {term?.meaning ?? term?.plain}
              </AppText>
            )}
            <View style={styles.example}>
              <AppText
                variant="medium"
                size={comfortable ? 20 : 14}
                lineHeight={comfortable ? 30 : 22}
                style={{ color: COLORS.primary }}
              >
                {easy ? "쉬운말 표현" : term?.example ? "예시" : "쉬운말 표현"}
              </AppText>
              <AppText
                size={comfortable ? 20 : 16}
                lineHeight={comfortable ? 30 : 26}
              >
                {easy ? term?.plain : (term?.example ?? term?.plain)}
              </AppText>
            </View>
          </ScrollView>
        </Animated.View>
      </View>
    </Modal>
  );
}
const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    justifyContent: "flex-end",
    alignItems: "center",
    paddingHorizontal: 16,
    backgroundColor: "rgba(36,59,83,0.24)",
  },
  sheet: {
    width: "100%",
    maxWidth: 500,
    backgroundColor: COLORS.surface,
    borderRadius: 24,
    overflow: "hidden",
    ...CARD_SHADOW,
  },
  content: { padding: 24, gap: 16 },
  handle: {
    width: 32,
    height: 4,
    borderRadius: 2,
    backgroundColor: COLORS.secondary,
    opacity: 0.3,
    alignSelf: "center",
  },
  header: { flexDirection: "row", gap: 12, alignItems: "center" },
  comfortableClose: {
    minWidth: 80,
    minHeight: 56,
    padding: 12,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: COLORS.soft,
    borderRadius: 12,
  },
  example: {
    backgroundColor: COLORS.soft,
    padding: 16,
    borderRadius: 12,
    gap: 4,
  },
});
