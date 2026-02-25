import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

const BG = "#0D1117";
const PANEL_BG = "#161B22";
const ACCENT = "#F7C948";
const WHITE = "#FFFFFF";
const KW_COLOR = "#FF7B72";
const VAL_COLOR = "#79C0FF";
const BASE_COLOR = "#E6EDF3";
const COMMENT_CLR = "#8B949E";

const CODE_LINES = [
  { text: "for (int i = 0; i < n-1; i++) {", color: KW_COLOR },
  { text: "  for (int j = 0; j < n-i-1; j++) {", color: KW_COLOR },
  { text: "    if (arr[j] > arr[j+1]) {", color: KW_COLOR },
  { text: "      int temp = arr[j];", color: VAL_COLOR },
  { text: "      arr[j] = arr[j+1];", color: BASE_COLOR },
  { text: "      arr[j+1] = temp;", color: BASE_COLOR },
  { text: "    }", color: BASE_COLOR },
  { text: "  }", color: BASE_COLOR },
  { text: "}", color: BASE_COLOR },
];

export const BubbleSortIntro: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Accent stripe slides in from left
  const stripeWidth = spring({
    frame,
    fps,
    from: 0,
    to: 18,
    durationInFrames: 15,
    config: { stiffness: 300, damping: 25 },
  });

  // Badge pops in
  const badgeScale = spring({
    frame: frame - 8,
    fps,
    from: 0,
    to: 1,
    durationInFrames: 20,
    config: { stiffness: 200, damping: 15 },
  });

  // Title lines stagger in
  const titleBubbleY = spring({
    frame: frame - 12,
    fps,
    from: 60,
    to: 0,
    durationInFrames: 25,
    config: { stiffness: 180, damping: 18 },
  });
  const titleBubbleOpacity = interpolate(frame, [12, 22], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const titleSortY = spring({
    frame: frame - 20,
    fps,
    from: 60,
    to: 0,
    durationInFrames: 25,
    config: { stiffness: 180, damping: 18 },
  });
  const titleSortOpacity = interpolate(frame, [20, 30], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Code panel slides in from right
  const panelX = spring({
    frame: frame - 18,
    fps,
    from: 200,
    to: 0,
    durationInFrames: 30,
    config: { stiffness: 120, damping: 20 },
  });
  const panelOpacity = interpolate(frame, [18, 30], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Code lines stagger
  const codeLineStart = 30;

  // Subtitle fades in
  const subtitleOpacity = interpolate(frame, [50, 65], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Exit: everything fades near the end
  const exitOpacity = interpolate(frame, [100, 118], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: BG,
        opacity: exitOpacity,
        fontFamily:
          "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
      }}
    >
      {/* Subtle gradient overlay */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: `linear-gradient(180deg, ${BG} 0%, #0F1923 100%)`,
        }}
      />

      {/* Left accent stripe */}
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          width: stripeWidth,
          height: "100%",
          backgroundColor: ACCENT,
        }}
      />

      {/* JAVA badge */}
      <div
        style={{
          position: "absolute",
          left: 60,
          top: 50,
          transform: `scale(${badgeScale})`,
          transformOrigin: "left center",
        }}
      >
        <div
          style={{
            backgroundColor: ACCENT,
            color: BG,
            fontSize: 34,
            fontWeight: 800,
            padding: "10px 24px",
            borderRadius: 10,
            letterSpacing: 2,
          }}
        >
          JAVA
        </div>
      </div>

      {/* Title: BUBBLE */}
      <div
        style={{
          position: "absolute",
          left: 60,
          top: 160,
          transform: `translateY(${titleBubbleY}px)`,
          opacity: titleBubbleOpacity,
        }}
      >
        <div
          style={{
            fontSize: 160,
            fontWeight: 900,
            color: WHITE,
            lineHeight: 1,
            textShadow: "4px 4px 0px rgba(0,0,0,0.5)",
          }}
        >
          BUBBLE
        </div>
      </div>

      {/* Title: SORT */}
      <div
        style={{
          position: "absolute",
          left: 60,
          top: 330,
          transform: `translateY(${titleSortY}px)`,
          opacity: titleSortOpacity,
        }}
      >
        <div
          style={{
            fontSize: 160,
            fontWeight: 900,
            color: WHITE,
            lineHeight: 1,
            textShadow: "4px 4px 0px rgba(0,0,0,0.5)",
          }}
        >
          SORT
        </div>
      </div>

      {/* Subtitle */}
      <div
        style={{
          position: "absolute",
          left: 60,
          top: 540,
          opacity: subtitleOpacity,
        }}
      >
        <div
          style={{
            fontSize: 30,
            fontWeight: 600,
            color: ACCENT,
            fontFamily: "'Liberation Sans', 'Helvetica Neue', sans-serif",
          }}
        >
          O(n²) Time Complexity · Build, Compile & Run
        </div>
      </div>

      {/* Code panel */}
      <div
        style={{
          position: "absolute",
          right: 80,
          top: 80,
          width: 680,
          height: 600,
          transform: `translateX(${panelX}px)`,
          opacity: panelOpacity,
          backgroundColor: PANEL_BG,
          borderRadius: 16,
          padding: "16px 0",
          overflow: "hidden",
        }}
      >
        {/* Window dots */}
        <div style={{ display: "flex", gap: 8, padding: "0 20px 16px" }}>
          {["#FF5F56", "#FFBD2E", "#27C93F"].map((c) => (
            <div
              key={c}
              style={{
                width: 12,
                height: 12,
                borderRadius: "50%",
                backgroundColor: c,
              }}
            />
          ))}
        </div>

        {/* Code lines */}
        <div style={{ padding: "0 20px" }}>
          {CODE_LINES.map((line, i) => {
            const lineDelay = codeLineStart + i * 3;
            const lineOpacity = interpolate(
              frame,
              [lineDelay, lineDelay + 8],
              [0, 1],
              {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
              }
            );
            const lineX = interpolate(
              frame,
              [lineDelay, lineDelay + 8],
              [20, 0],
              {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
              }
            );
            return (
              <div
                key={i}
                style={{
                  fontSize: 24,
                  lineHeight: "38px",
                  color: line.color,
                  opacity: lineOpacity,
                  transform: `translateX(${lineX}px)`,
                  whiteSpace: "pre",
                }}
              >
                {line.text}
              </div>
            );
          })}
        </div>
      </div>
    </AbsoluteFill>
  );
};
