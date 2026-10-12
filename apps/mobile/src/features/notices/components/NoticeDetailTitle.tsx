import { useState } from "react";
import { Platform, StyleSheet, useWindowDimensions, View } from "react-native";
import { useDisplayPreferences } from "@/shared/accessibility/displayPreferences";
import { AppText } from "@/shared/ui/AppText";

const TITLE_SIZE = 28;
const TITLE_LINE_HEIGHT = 38;
const COMPACT_TITLE_SIZE = 24;
const COMPACT_TITLE_LINE_HEIGHT = 33;
const MAX_TITLE_LINES = 3;

export function NoticeDetailTitle({ title }: { title: string }) {
  const fontScale = useDisplayPreferences((state) => state.fontScale);
  const { fontScale: systemFontScale } = useWindowDimensions();
  const [width, setWidth] = useState(0);
  const [measurement, setMeasurement] = useState({ key: "", compact: false });
  const measurementKey = JSON.stringify([
    title,
    width,
    fontScale,
    systemFontScale,
  ]);
  const compact = measurement.key === measurementKey && measurement.compact;

  function updateMeasurement(needsCompact: boolean) {
    setMeasurement((previous) =>
      previous.key === measurementKey && previous.compact === needsCompact
        ? previous
        : { key: measurementKey, compact: needsCompact },
    );
  }

  return (
    <View
      style={styles.container}
      onLayout={(event) => setWidth(event.nativeEvent.layout.width)}
    >
      {/* 기본 크기로 별도 측정해 축소된 제목의 줄 수 때문에 크기가 반복 변경되지 않게 한다. */}
      <AppText
        key={measurementKey}
        variant="display"
        size={TITLE_SIZE}
        lineHeight={TITLE_LINE_HEIGHT}
        style={styles.measurement}
        accessible={false}
        accessibilityElementsHidden
        importantForAccessibility="no-hide-descendants"
        aria-hidden
        pointerEvents="none"
        onTextLayout={
          Platform.OS === "web"
            ? undefined
            : (event) => {
                updateMeasurement(
                  event.nativeEvent.lines.length > MAX_TITLE_LINES,
                );
              }
        }
        onLayout={
          Platform.OS !== "web"
            ? undefined
            : (event) => {
                // React Native Web에는 onTextLayout이 없어 기본 줄 높이로 줄 수를 판단한다.
                updateMeasurement(
                  event.nativeEvent.layout.height >
                    TITLE_LINE_HEIGHT * fontScale * (MAX_TITLE_LINES + 0.5),
                );
              }
        }
      >
        {title}
      </AppText>
      <AppText
        variant="display"
        size={compact ? COMPACT_TITLE_SIZE : TITLE_SIZE}
        lineHeight={compact ? COMPACT_TITLE_LINE_HEIGHT : TITLE_LINE_HEIGHT}
      >
        {title}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  measurement: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    opacity: 0,
  },
});
