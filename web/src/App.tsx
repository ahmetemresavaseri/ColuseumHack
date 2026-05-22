import { useState } from "react";
import CallWall from "./CallWall";
import BackendMap from "./components/BackendMap";

type View = "wall" | "map";

export default function App() {
  const [view, setView] = useState<View>("wall");
  return (
    <>
      <nav className="bmap-tabs" role="tablist" aria-label="View">
        <div className="segControl">
          <button
            type="button"
            role="tab"
            aria-selected={view === "wall"}
            className={view === "wall" ? "segControl-on" : ""}
            onClick={() => setView("wall")}
          >
            Live Call
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === "map"}
            className={view === "map" ? "segControl-on" : ""}
            onClick={() => setView("map")}
          >
            Backend System Map
          </button>
        </div>
      </nav>
      {view === "wall" ? <CallWall /> : <BackendMap />}
    </>
  );
}
