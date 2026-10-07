import { StyleSheet, View } from "react-native";
import { Image } from "expo-image";
import { AppText } from "@/shared/ui/AppText";
import { COLORS } from "@/shared/theme/tokens";
import { type Notice } from "../types/notice";

// 고정 좌표는 Figma 그림 레이어 안에서만 사용하며, 화면 너비에 따른 축소는 HomeScreen이 담당한다.
export function ClosedEnvelope() {
  return (
    <View style={styles.closed}>
      <Image
        source={require("@/assets/figma/home-img.svg")}
        style={{
          position: "absolute",
          left: 6.692,
          top: 7.615,
          width: 300.462,
          height: 169.385,
        }}
      />
      <Image
        source={require("@/assets/figma/home-img1.svg")}
        style={{
          position: "absolute",
          left: 8.997,
          top: 84.66,
          width: 295.847,
          height: 89.5385,
        }}
      />
      <Image
        source={require("@/assets/figma/home-img2.svg")}
        style={{
          position: "absolute",
          left: 9.461,
          top: 7.615,
          width: 294.923,
          height: 101.538,
        }}
      />
    </View>
  );
}
/** 봉투 뒤·편지·문구·봉투 앞·캐릭터 순으로 쌓아 문구를 이미지 없이 실제 텍스트로 표시한다. */
export function LetterIllustration({ notice }: { notice: Notice }) {
  return (
    <View style={styles.art}>
      <Image
        source={require("@/assets/figma/home-imgEnvelopeBack.svg")}
        style={{
          position: "absolute",
          left: 6.692,
          top: 195.446,
          width: 300.462,
          height: 266.769,
        }}
      />
      <Image
        source={require("@/assets/figma/home-imgLetterPaper.svg")}
        style={{
          position: "absolute",
          left: 25.154,
          top: 153.923,
          width: 263.538,
          height: 280.615,
        }}
      />
      <View style={styles.copy}>
        <View style={styles.chip}>
          <AppText
            variant="bold"
            size={11.08}
            style={{ color: COLORS.primary }}
          >
            {notice.category}
          </AppText>
        </View>
        <View
          accessible
          accessibilityLabel={notice.title}
          style={{ flexDirection: "row", flexWrap: "wrap" }}
        >
          {/* 제목을 어절 단위로 배치해 한글 단어 중간에서 줄이 나뉘는 것을 줄인다. */}
          {notice.title.split(" ").map((word, index) => (
            <AppText
              key={index}
              variant="display"
              size={20.31}
              lineHeight={25.846}
            >
              {word}{" "}
            </AppText>
          ))}
        </View>
        <AppText secondary size={11.08} numberOfLines={1}>
          {notice.publishedAt} · {notice.provider}
        </AppText>
        <View style={styles.fact}>
          <Image
            source={require("@/assets/figma/home-imgCalendarIcon.svg")}
            style={styles.factIcon}
          />
          <AppText
            variant="bold"
            size={11.08}
            style={{ color: COLORS.primary }}
          >
            기한
          </AppText>
          <AppText size={11.08} style={{ flex: 1 }}>
            {notice.deadline}
          </AppText>
        </View>
        <View style={styles.fact}>
          <Image
            source={require("@/assets/figma/home-imgPersonIcon.svg")}
            style={styles.factIcon}
          />
          <AppText
            variant="bold"
            size={11.08}
            style={{ color: COLORS.primary }}
          >
            대상
          </AppText>
          <AppText size={11.08} numberOfLines={1} style={{ flex: 1 }}>
            {notice.audience}
          </AppText>
        </View>
      </View>
      <Image
        source={require("@/assets/figma/home-imgEnvelopeFront.svg")}
        style={{
          position: "absolute",
          left: 6.692,
          top: 298.446,
          width: 300.462,
          height: 163.846,
        }}
      />
      <View pointerEvents="none" style={StyleSheet.absoluteFill}>
        <Image
          source={require("@/assets/figma/home-imgTail.svg")}
          style={{
            position: "absolute",
            left: 21.8,
            top: 100.94,
            width: 81.1968,
            height: 59.8828,
          }}
        />
        <Image
          source={require("@/assets/figma/home-imgUpperBody.svg")}
          style={{
            position: "absolute",
            left: 0,
            top: 1.85,
            width: 313.846,
            height: 470.769,
          }}
        />
        <Image
          source={require("@/assets/figma/home-img04ScarfTails.svg")}
          style={{
            position: "absolute",
            left: 225.07,
            top: 132.82,
            width: 54.1508,
            height: 58.4918,
          }}
        />
        <Image
          source={require("@/assets/figma/home-img05ScarfNeck.svg")}
          style={{
            position: "absolute",
            left: 108.17,
            top: 131.92,
            width: 165.978,
            height: 56.3703,
          }}
        />
        <Image
          source={require("@/assets/figma/home-img06Head.svg")}
          style={{
            position: "absolute",
            left: 97.13,
            top: 13.52,
            width: 190.812,
            height: 145.178,
            transform: [{ rotate: "14.38deg" }],
          }}
        />
        <Image
          source={require("@/assets/figma/home-img07EarInteriors.svg")}
          style={{
            position: "absolute",
            left: 129.37,
            top: 28.81,
            width: 124.501,
            height: 35.5805,
            transform: [{ rotate: "14.38deg" }],
          }}
        />
        <Image
          source={require("@/assets/figma/home-img09Cheeks.svg")}
          style={{
            position: "absolute",
            left: 94.02,
            top: 91.93,
            width: 177.54,
            height: 59.1638,
            transform: [{ rotate: "14.38deg" }],
          }}
        />
        <Image
          source={require("@/assets/figma/home-img08EyeMasks.svg")}
          style={{
            position: "absolute",
            left: 120.48,
            top: 78.14,
            width: 133.597,
            height: 54.8278,
            transform: [{ rotate: "14.38deg" }],
          }}
        />
        <Image
          source={require("@/assets/figma/home-img10EyesAndNose.svg")}
          style={{
            position: "absolute",
            left: 151.96,
            top: 87.74,
            width: 70.8061,
            height: 31.4017,
            transform: [{ rotate: "14.38deg" }],
          }}
        />
        <Image
          source={require("@/assets/figma/home-imgVector2.svg")}
          style={{
            position: "absolute",
            left: 117.22,
            top: 149.09,
            width: 28.4981,
            height: 13.573,
          }}
        />
        <Image
          source={require("@/assets/figma/home-imgLeftPaw.svg")}
          style={{
            position: "absolute",
            left: 96.58,
            top: 129.28,
            width: 49.8966,
            height: 31.2831,
          }}
        />
        <Image
          source={require("@/assets/figma/home-imgRightPaw.svg")}
          style={{
            position: "absolute",
            left: 194.54,
            top: 137.24,
            width: 47.1643,
            height: 30.318,
          }}
        />
      </View>
    </View>
  );
}
const styles = StyleSheet.create({
  art: { width: 313.846, height: 470.769 },
  closed: { width: 313.846, height: 184.615 },
  copy: {
    position: "absolute",
    left: 59.08,
    top: 178.15,
    width: 195.692,
    gap: 7.385,
  },
  chip: {
    backgroundColor: COLORS.soft,
    borderRadius: 999,
    paddingHorizontal: 9.231,
    paddingVertical: 3.692,
    alignSelf: "flex-start",
  },
  fact: { flexDirection: "row", alignItems: "center", gap: 4 },
  factIcon: { width: 14.7692, height: 14.7692 },
});
