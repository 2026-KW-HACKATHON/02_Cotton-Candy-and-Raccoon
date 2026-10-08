import { useState } from "react";
import {
  Modal,
  ScrollView,
  StyleSheet,
  View,
  useWindowDimensions,
} from "react-native";
import { AppText } from "@/shared/ui/AppText";
import { COLORS } from "@/shared/theme/tokens";
import { type NoticeFile } from "../types/notice";
import { useNoticeFilePreviews } from "../hooks/useNoticeFilePreviews";
import { useNoticeFileActions } from "../hooks/useNoticeFileActions";
import { NoticeActionButton } from "./NoticeActionButton";
import { NoticeImage } from "./NoticeImage";
export function NoticeFiles({
  files = [],
  sourceUrl,
  comfortable = false,
}: {
  files?: NoticeFile[];
  sourceUrl?: string;
  comfortable?: boolean;
}) {
  const [listOpen, setListOpen] = useState(false);
  const [preview, setPreview] = useState<NoticeFile | null>(null);
  const [zoom, setZoom] = useState(false);
  const { width } = useWindowDimensions();
  const actions = useNoticeFileActions();
  const attachments = files.filter((file) => file.kind === "attachment");
  const filePreviews = useNoticeFilePreviews(attachments, listOpen);
  const images = files.filter((file) => file.kind === "inline_image");
  const size = comfortable ? 20 : 16;
  function showPreview(file: NoticeFile) {
    actions.clear();
    setPreview(file);
    setZoom(false);
  }
  return (
    <View style={{ gap: 16 }}>
      {images.length > 0 && (
        <View style={styles.card}>
          <AppText variant="bold" size={comfortable ? 24 : 18}>
            본문 이미지
          </AppText>
          <AppText size={size} secondary>
            이미지로 제공된 공문 내용을 확인해 주세요.
          </AppText>
          {images.map((file, index) => (
            <NoticeImage
              key={file.id}
              url={file.url}
              label={"본문 이미지 " + (index + 1)}
              comfortable={comfortable}
              onOpen={() => showPreview(file)}
            />
          ))}
        </View>
      )}
      {sourceUrl && (
        <NoticeActionButton
          label="공식 원문 보기 ↗"
          comfortable={comfortable}
          disabled={actions.busy}
          onPress={() => {
            void actions.open(sourceUrl);
          }}
        />
      )}
      <NoticeActionButton
        label={
          attachments.length
            ? "첨부 파일 보기 · " + attachments.length + "개"
            : "첨부 파일 보기"
        }
        comfortable={comfortable}
        onPress={() => {
          actions.clear();
          setListOpen(true);
        }}
      />
      {Boolean(actions.message) && !listOpen && !preview && (
        <AppText size={size} accessibilityLiveRegion="polite">
          {actions.message}
        </AppText>
      )}
      <Modal
        visible={listOpen && !preview}
        transparent
        animationType="slide"
        onRequestClose={() => setListOpen(false)}
      >
        <View style={styles.overlay}>
          <View accessibilityViewIsModal style={styles.sheet}>
            <AppText variant="bold" size={comfortable ? 24 : 20}>
              첨부 파일
            </AppText>
            <ScrollView contentContainerStyle={{ gap: 16 }}>
              {!attachments.length && (
                <AppText size={size}>첨부된 파일이 없어요.</AppText>
              )}
              {attachments.map((file, index) => {
                const { kind, checking } = filePreviews[index];
                return (
                  <View key={file.id} style={styles.card}>
                    <AppText variant="bold" size={size}>
                      첨부 {index + 1}
                    </AppText>
                    {kind === "unknown" && (
                      <AppText
                        secondary
                        size={size}
                        accessibilityLiveRegion="polite"
                      >
                        {checking
                          ? "파일 형식을 확인하고 있어요."
                          : "파일 형식을 확인하지 못했어요. ‘파일 직접 열기’에서 확인해 주세요."}
                      </AppText>
                    )}
                    {kind === "unsupported" && (
                      <AppText secondary size={size}>
                        앱 안에서 미리보기 어려운 형식이에요. 파일을 내려받아
                        열어 주세요.
                      </AppText>
                    )}
                    {(kind === "image" || kind === "pdf") && (
                      <NoticeActionButton
                        label={kind === "pdf" ? "PDF 미리보기 ↗" : "미리보기"}
                        comfortable={comfortable}
                        disabled={actions.busy}
                        onPress={() => {
                          if (kind === "image") showPreview(file);
                          else void actions.open(file.url);
                        }}
                      />
                    )}
                    <NoticeActionButton
                      label={actions.busy ? "파일 처리 중…" : "다운로드"}
                      comfortable={comfortable}
                      filled
                      disabled={actions.busy}
                      onPress={() => {
                        void actions.download(file);
                      }}
                    />
                    <NoticeActionButton
                      label="파일 직접 열기 ↗"
                      comfortable={comfortable}
                      disabled={actions.busy}
                      onPress={() => {
                        void actions.open(file.url);
                      }}
                    />
                  </View>
                );
              })}
              {Boolean(actions.message) && (
                <AppText size={size} accessibilityLiveRegion="polite">
                  {actions.message}
                </AppText>
              )}
              {sourceUrl && (
                <NoticeActionButton
                  label="공식 원문에서 확인 ↗"
                  comfortable={comfortable}
                  disabled={actions.busy}
                  onPress={() => {
                    void actions.open(sourceUrl);
                  }}
                />
              )}
            </ScrollView>
            <NoticeActionButton
              label="닫기"
              comfortable={comfortable}
              disabled={actions.busy}
              onPress={() => setListOpen(false)}
            />
          </View>
        </View>
      </Modal>
      <Modal
        visible={preview !== null}
        transparent
        animationType="fade"
        onRequestClose={() => setPreview(null)}
      >
        <View style={styles.overlay}>
          <View accessibilityViewIsModal style={styles.sheet}>
            <AppText variant="bold" size={comfortable ? 24 : 20}>
              이미지 미리보기
            </AppText>
            <NoticeActionButton
              label={zoom ? "기본 크기" : "확대"}
              comfortable={comfortable}
              onPress={() => setZoom((v) => !v)}
            />
            <ScrollView>
              <ScrollView horizontal>
                <View
                  style={{
                    width:
                      Math.max(240, Math.min(width - 80, 460)) * (zoom ? 2 : 1),
                  }}
                >
                  {preview && (
                    <NoticeImage
                      key={preview.id}
                      url={preview.url}
                      label="선택한 이미지"
                      comfortable={comfortable}
                    />
                  )}
                </View>
              </ScrollView>
            </ScrollView>
            {Boolean(actions.message) && (
              <AppText size={size} accessibilityLiveRegion="polite">
                {actions.message}
              </AppText>
            )}
            {preview && (
              <NoticeActionButton
                label="파일 직접 열기 ↗"
                comfortable={comfortable}
                disabled={actions.busy}
                onPress={() => {
                  void actions.open(preview.url);
                }}
              />
            )}
            {preview && (
              <NoticeActionButton
                label={actions.busy ? "파일 처리 중…" : "다운로드"}
                comfortable={comfortable}
                filled
                disabled={actions.busy}
                onPress={() => {
                  void actions.download(preview);
                }}
              />
            )}
            <NoticeActionButton
              label="닫기"
              comfortable={comfortable}
              disabled={actions.busy}
              onPress={() => setPreview(null)}
            />
          </View>
        </View>
      </Modal>
    </View>
  );
}
const styles = StyleSheet.create({
  card: {
    backgroundColor: COLORS.soft,
    borderRadius: 16,
    padding: 16,
    gap: 12,
  },
  overlay: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    padding: 20,
    backgroundColor: "rgba(36,59,83,.24)",
  },
  sheet: {
    width: "100%",
    maxWidth: 500,
    maxHeight: "90%",
    backgroundColor: COLORS.surface,
    borderRadius: 20,
    padding: 20,
    gap: 16,
  },
});
