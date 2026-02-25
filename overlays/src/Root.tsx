import { Composition } from "remotion";
import { BubbleSortIntro } from "./scenes/BubbleSortIntro";
import { BubbleSortOutro } from "./scenes/BubbleSortOutro";

const FPS = 30;

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="BubbleSortIntro"
        component={BubbleSortIntro}
        durationInFrames={4 * FPS}
        fps={FPS}
        width={1920}
        height={1080}
      />
      <Composition
        id="BubbleSortOutro"
        component={BubbleSortOutro}
        durationInFrames={5 * FPS}
        fps={FPS}
        width={1920}
        height={1080}
      />
    </>
  );
};
