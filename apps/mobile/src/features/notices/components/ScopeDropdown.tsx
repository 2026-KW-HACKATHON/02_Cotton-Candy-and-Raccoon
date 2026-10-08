import { useEffect, useRef, useState } from "react";
import {
  Dimensions,
  Modal,
  Pressable,
  StyleSheet,
  View,
  useWindowDimensions,
} from "react-native";
import { Image } from "expo-image";
import { AppText } from "@/shared/ui/AppText";
import { COLORS } from "@/shared/theme/tokens";
import { NOTICE_SCOPES, useNoticeScopeStore } from "../store/noticeScopeStore";

const ICONS = {
  down: require("@/assets/figma/scope-dropdown/chevron-down.svg"),
  up: require("@/assets/figma/scope-dropdown/chevron-up.svg"),
  check: require("@/assets/figma/scope-dropdown/check.svg"),
};

export function ScopeDropdown() {
  const source = useNoticeScopeStore((state) => state.source);
  const scope =
    NOTICE_SCOPES.find((option) => option.source === source)?.label ?? "전체";
  const setSource = useNoticeScopeStore((state) => state.setSource);
  const anchor = useRef<View>(null);
  const { width, height } = useWindowDimensions();
  const [position, setPosition] = useState<{
    left: number;
    top: number;
  } | null>(null);
  useEffect(() => {
    const subscription = Dimensions.addEventListener("change", () =>
      setPosition(null),
    );
    return () => subscription.remove();
  }, []);
  function open() {
    anchor.current?.measureInWindow((x, y, anchorWidth, anchorHeight) => {
      setPosition({
        left: Math.max(12, Math.min(width - 176, x + anchorWidth - 164)),
        top: Math.max(
          12,
          Math.min(
            height - (NOTICE_SCOPES.length * 48 + 16) - 12,
            y + anchorHeight + 7,
          ),
        ),
      });
    });
  }
  return (
    <View ref={anchor} collapsable={false}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`공문 지역 선택, ${scope}`}
        accessibilityState={{ expanded: position !== null }}
        aria-haspopup="menu"
        onPress={open}
        style={[styles.trigger, position !== null && styles.expanded]}
      >
        <AppText
          size={14}
          lineHeight={22}
          style={{ color: position ? COLORS.primary : COLORS.secondary }}
        >
          {scope}
        </AppText>
        <Image source={position ? ICONS.up : ICONS.down} style={styles.icon} />
      </Pressable>
      <Modal
        visible={position !== null}
        transparent
        animationType="none"
        onRequestClose={() => setPosition(null)}
      >
        <View style={styles.overlay}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="지역 선택 메뉴 닫기"
            onPress={() => setPosition(null)}
            style={StyleSheet.absoluteFill}
          />
          {position && (
            <View
              accessibilityRole="menu"
              accessibilityLabel="공문 지역"
              accessibilityViewIsModal
              style={[styles.menu, position]}
            >
              {NOTICE_SCOPES.map((option) => (
                <Pressable
                  key={option.label}
                  accessibilityRole="menuitem"
                  accessibilityState={{ selected: option.source === source }}
                  onPress={() => {
                    setSource(option.source);
                    setPosition(null);
                  }}
                  style={[
                    styles.option,
                    option.source === source && styles.selected,
                  ]}
                >
                  <AppText
                    size={14}
                    lineHeight={22}
                    style={{
                      color:
                        option.source === source
                          ? COLORS.primary
                          : COLORS.secondary,
                    }}
                  >
                    {option.label}
                  </AppText>
                  {option.source === source && (
                    <Image source={ICONS.check} style={styles.icon} />
                  )}
                </Pressable>
              ))}
            </View>
          )}
        </View>
      </Modal>
    </View>
  );
}
const styles = StyleSheet.create({
  trigger: {
    minWidth: 104,
    minHeight: 48,
    paddingHorizontal: 12,
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: 999,
    backgroundColor: COLORS.surface,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
  },
  expanded: { borderColor: COLORS.primary },
  icon: { width: 16, height: 16 },
  overlay: { flex: 1 },
  menu: {
    position: "absolute",
    width: 164,
    paddingVertical: 8,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: COLORS.border,
    backgroundColor: COLORS.surface,
    overflow: "hidden",
    shadowColor: COLORS.text,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.08,
    shadowRadius: 6,
    elevation: 4,
  },
  option: {
    minHeight: 48,
    paddingHorizontal: 12,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  selected: { backgroundColor: COLORS.soft },
});
