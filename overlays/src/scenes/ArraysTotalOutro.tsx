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
const MUTED = "#8B949E";
const BASE_COLOR = "#E6EDF3";

const TOPICS = [
  "Classic for-loop accumulator pattern",
  "Enhanced for-each loop syntax",
  "Guard clauses for null & empty arrays",
  "Defensive programming techniques",
];

export const ArraysTotalOutro: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Entrance: content fades in
  const entranceOpacity = interpolate(frame, [0, 15], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Title spring in
  const titleY = spring({
    frame,
    fps,
    from: 40,
    to: 0,
    durationInFrames: 25,
    config: { stiffness: 180, damping: 18 },
  });

  // Divider line wipe
  const dividerWidth = spring({
    frame: frame - 10,
    fps,
    from: 0,
    to: 560,
    durationInFrames: 30,
    config: { stiffness: 120, damping: 25 },
  });

  // Topics stagger
  const topicBaseDelay = 20;

  // Bottom section
  const bottomOpacity = interpolate(frame, [70, 85], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Next video CTA pulse
  const ctaScale = spring({
    frame: frame - 80,
    fps,
    from: 0.9,
    to: 1,
    durationInFrames: 25,
    config: { stiffness: 200, damping: 12 },
  });

  // Exit fade
  const exitOpacity = interpolate(frame, [130, 148], [1, 0], {
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
      {/* Gradient */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: `linear-gradient(180deg, #0F1923 0%, ${BG} 100%)`,
        }}
      />

      {/* Left accent stripe */}
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          width: 18,
          height: "100%",
          backgroundColor: ACCENT,
        }}
      />

      {/* Main content */}
      <div
        style={{
          position: "absolute",
          left: 120,
          top: 0,
          width: 1680,
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          opacity: entranceOpacity,
        }}
      >
        {/* Title */}
        <div
          style={{
            transform: `translateY(${titleY}px)`,
            marginBottom: 20,
          }}
        >
          <div
            style={{
              fontSize: 72,
              fontWeight: 900,
              color: WHITE,
              lineHeight: 1.1,
              textShadow: "3px 3px 0px rgba(0,0,0,0.4)",
            }}
          >
            What We Covered
          </div>
        </div>

        {/* Divider */}
        <div
          style={{
            width: dividerWidth,
            height: 4,
            backgroundColor: ACCENT,
            borderRadius: 2,
            marginBottom: 40,
          }}
        />

        {/* Topics list */}
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {TOPICS.map((topic, i) => {
            const delay = topicBaseDelay + i * 10;
            const topicOpacity = interpolate(
              frame,
              [delay, delay + 12],
              [0, 1],
              {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
              }
            );
            const topicX = interpolate(
              frame,
              [delay, delay + 12],
              [30, 0],
              {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
              }
            );

            return (
              <div
                key={i}
                style={{
                  opacity: topicOpacity,
                  transform: `translateX(${topicX}px)`,
                  display: "flex",
                  alignItems: "center",
                  gap: 16,
                }}
              >
                <div
                  style={{
                    width: 10,
                    height: 10,
                    borderRadius: "50%",
                    backgroundColor: ACCENT,
                    flexShrink: 0,
                  }}
                />
                <div
                  style={{
                    fontSize: 34,
                    fontWeight: 500,
                    color: BASE_COLOR,
                    fontFamily:
                      "'Liberation Sans', 'Helvetica Neue', sans-serif",
                  }}
                >
                  {topic}
                </div>
              </div>
            );
          })}
        </div>

        {/* Bottom section: complexity badge + next video CTA */}
        <div
          style={{
            marginTop: 60,
            display: "flex",
            alignItems: "center",
            gap: 40,
            opacity: bottomOpacity,
          }}
        >
          {/* Complexity badge */}
          <div
            style={{
              backgroundColor: PANEL_BG,
              borderRadius: 12,
              padding: "16px 28px",
              border: `2px solid ${ACCENT}33`,
            }}
          >
            <div
              style={{
                fontSize: 22,
                color: MUTED,
                marginBottom: 4,
              }}
            >
              Complexity
            </div>
            <div
              style={{
                fontSize: 36,
                fontWeight: 800,
                color: ACCENT,
              }}
            >
              O(n)
            </div>
          </div>

          {/* Divider */}
          <div
            style={{
              width: 2,
              height: 60,
              backgroundColor: MUTED + "44",
            }}
          />

          {/* Next video CTA */}
          <div style={{ transform: `scale(${ctaScale})` }}>
            <div
              style={{
                fontSize: 22,
                color: MUTED,
                marginBottom: 4,
                fontFamily:
                  "'Liberation Sans', 'Helvetica Neue', sans-serif",
              }}
            >
              Up next
            </div>
            <div
              style={{
                fontSize: 36,
                fontWeight: 700,
                color: WHITE,
                fontFamily:
                  "'Liberation Sans', 'Helvetica Neue', sans-serif",
              }}
            >
              Java Sorting Algorithms →
            </div>
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};
