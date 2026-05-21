import { BrainPane } from "./components/BrainPane";
import { CallStatusCard } from "./components/CallStatusCard";
import { CitationsPane } from "./components/CitationsPane";
import { DemoControls } from "./components/DemoControls";
import { SlotsPane } from "./components/SlotsPane";
import { TranscriptPane } from "./components/TranscriptPane";
import { mockCall } from "./lib/mockEvents";

export default function CallWall() {
  return (
    <main className="wall">
      <header className="wallHeader">
        <div>
          <p className="eyebrow">Atrium Live Call Wall</p>
          <h1>{mockCall.companyName}</h1>
        </div>
        <DemoControls />
      </header>
      <section className="grid">
        <CallStatusCard call={mockCall} />
        <TranscriptPane turns={mockCall.transcript} />
        <SlotsPane slots={mockCall.slots} />
        <BrainPane brain={mockCall.brain} />
        <CitationsPane citations={mockCall.citations} />
      </section>
    </main>
  );
}
