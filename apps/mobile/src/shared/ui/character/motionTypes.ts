export type MotionKeyframe = {
  at: number;
  value: number;
  easing: "linear" | "ease-in-out" | "ease-out";
};
export type MotionTracks = Partial<
  Record<
    "opacity" | "rotate" | "x" | "y" | "scaleX" | "scaleY",
    MotionKeyframe[]
  >
>;
export type MotionLayer = {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  asset?: string;
  rotation?: number;
  tracks?: MotionTracks;
  children?: MotionLayer[];
};
export type MotionScene = {
  width: number;
  height: number;
  durationMs: number;
  layers: MotionLayer[];
  extras: Record<string, MotionTracks>;
};
