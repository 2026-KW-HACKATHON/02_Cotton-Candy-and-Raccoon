import { Text, type TextProps } from "react-native";
import { COLORS, FONTS } from "@/shared/theme/tokens";
import { useDisplayPreferences } from "@/shared/accessibility/displayPreferences";

type Props = TextProps & {
  variant?: "body" | "display" | "bold" | "medium";
  size?: number;
  lineHeight?: number;
  secondary?: boolean;
};
export function AppText({
  variant = "body",
  size = 14.77,
  lineHeight = size * 1.5,
  secondary,
  style,
  ...props
}: Props) {
  // 앱 설정의 배율을 기본 글자 크기와 줄 높이에 반영한다. OS 글자 확대는 Text 기본 동작을 따른다.
  const fontScale = useDisplayPreferences((state) => state.fontScale);
  return (
    <Text
      {...props}
      style={[
        {
          fontFamily: FONTS[variant],
          color: secondary ? COLORS.secondary : COLORS.text,
          fontSize: size * fontScale,
          lineHeight: lineHeight * fontScale,
        },
        style,
      ]}
    />
  );
}
