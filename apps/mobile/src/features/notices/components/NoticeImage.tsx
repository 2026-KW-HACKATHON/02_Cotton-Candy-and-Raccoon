import { useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { Image } from "expo-image";
import { AppText } from "@/shared/ui/AppText";
import { COLORS } from "@/shared/theme/tokens";
import { NoticeActionButton } from "./NoticeActionButton";
export function NoticeImage({
  url,
  label,
  comfortable = false,
  onOpen,
}: {
  url: string;
  label: string;
  comfortable?: boolean;
  onOpen?: () => void;
}) {
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);
  const [ratio, setRatio] = useState(1);
  return (
    <View style={{ gap: 12 }}>
      <AppText variant="bold" size={comfortable ? 20 : 16}>
        {label}
      </AppText>
      {failed ? (
        <View
          style={{
            backgroundColor: COLORS.soft,
            padding: 16,
            gap: 12,
            borderRadius: 12,
          }}
        >
          <AppText size={comfortable ? 20 : 16}>
            이미지를 불러오지 못했어요.
          </AppText>
          <NoticeActionButton
            comfortable={comfortable}
            label="이미지 다시 불러오기"
            onPress={() => {
              setFailed(false);
              setLoading(true);
              setVersion((v) => v + 1);
            }}
          />
        </View>
      ) : (
        <View>
          <Image
            key={version}
            source={{ uri: url }}
            accessibilityLabel={label}
            accessible
            contentFit="contain"
            style={{
              width: "100%",
              aspectRatio: ratio,
              backgroundColor: COLORS.soft,
              borderRadius: 12,
            }}
            onLoad={(event) => {
              if (event.source.width > 0 && event.source.height > 0)
                setRatio(event.source.width / event.source.height);
            }}
            onError={() => {
              setFailed(true);
              setLoading(false);
            }}
            onLoadEnd={() => setLoading(false)}
          />
          {loading && (
            <ActivityIndicator
              accessibilityLabel="이미지 불러오는 중"
              color={COLORS.primary}
              style={{ position: "absolute", top: "50%", left: "50%" }}
            />
          )}
        </View>
      )}
      {onOpen && (
        <NoticeActionButton
          comfortable={comfortable}
          label="크게 보기"
          onPress={onOpen}
        />
      )}
    </View>
  );
}
