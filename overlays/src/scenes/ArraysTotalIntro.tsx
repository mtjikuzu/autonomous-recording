import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

const BG = "#0D1117";
const PANEL_BG = "#161B22";
const ACCENT = "#58A6FF";
const WHITE = "#FFFFFF";
const KW_COLOR = "#FF7B72";
const VAL_COLOR = "#79C0FF";
const BASE_COLOR = "#E6EDF3";
const COMMENT_CLR = "#8B949E";

const CODE_LINES = [
  { text: "int total = 0;", color: VAL_COLOR },
  { text: "for (int num : arr) {", color: KW_COLOR },
  { text: "    total += num;", color: VAL_COLOR },
  { text: "}", color: BASE_COLOR },
  { text: "return total;", color: KW_COLOR },
  { text: "", color: BASE_COLOR },
  { text: "// {1,2,3,4,5,6} → 21", color: COMMENT_CLR },
];

export const ArraysTotalIntro: React.FC = () => {
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
  const titleArrayY = spring({
    frame: frame - 12,
    fps,
    from: 60,
    to: 0,
    durationInFrames: 25,
    config: { stiffness: 180, damping: 18 },
  });
  const titleArrayOpacity = interpolate(frame, [12, 22], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const titleSumY = spring({
    frame: frame - 20,
    fps,
    from: 60,
    to: 0,
    durationInFrames: 25,
    config: { stiffness: 180, damping: 18 },
  });
  const titleSumOpacity = interpolate(frame, [20, 30], [0, 1], {
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

      {/* Title: ARRAY */}
      <div
        style={{
          position: "absolute",
          left: 60,
          top: 160,
          transform: `translateY(${titleArrayY}px)`,
          opacity: titleArrayOpacity,
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
          ARRAY
        </div>
      </div>

      {/* Title: SUM */}
      <div
        style={{
          position: "absolute",
          left: 60,
          top: 330,
          transform: `translateY(${titleSumY}px)`,
          opacity: titleSumOpacity,
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
          SUM
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
          Accumulator Pattern · For Loop & For-Each
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
