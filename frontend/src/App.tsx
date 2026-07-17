import { useEffect, useState } from "react";
import { Moon, Sun, UploadCloud, MessagesSquare, Share2, Wrench, ShieldCheck, Activity, LayoutDashboard } from "lucide-react";
import { Loading } from "./components/Loading";
import { Ingest } from "./components/Ingest";
import { Ask } from "./components/Ask";
import { Graph } from "./components/Graph";
import { RCA } from "./components/RCA";
import { Compliance } from "./components/Compliance";
import { Failures } from "./components/Failures";
import { Admin } from "./components/Admin";
import { Button } from "./components/ui/button";
import { health } from "./lib/api";
import { cn } from "./lib/utils";

const TABS = [
  { id: "ingest", label: "Ingest", icon: UploadCloud },
  { id: "ask", label: "Ask", icon: MessagesSquare },
  { id: "graph", label: "Graph", icon: Share2 },
  { id: "rca", label: "RCA", icon: Wrench },
  { id: "compliance", label: "Compliance", icon: ShieldCheck },
  { id: "failures", label: "Failures", icon: Activity },
  { id: "admin", label: "Admin", icon: LayoutDashboard },
] as const;

export default function App() {
  const [booting, setBooting] = useState(true);
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("ask");
  const [dark, setDark] = useState(() => localStorage.getItem("theme") !== "light");

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("theme", dark ? "dark" : "light");
  }, [dark]);

  // Boot: wait for the backend health check, but hold the themed screen a beat.
  useEffect(() => {
    const start = Date.now();
    health().finally(() => {
      const wait = Math.max(0, 1400 - (Date.now() - start));
      setTimeout(() => setBooting(false), wait);
    });
  }, []);

  if (booting) return <Loading />;

  return (
    <div className="flex min-h-screen flex-col px-6 lg:px-10">
      {/* flex-wrap: with 6 tabs the nav no longer fits beside the wordmark at
          375px — it drops to a second row instead of overflowing the page. */}
      <header className="flex flex-wrap items-center gap-3 border-b border-border py-4">
        <div className="flex flex-col">
          <h1 className="text-lg font-bold leading-none tracking-tight">
            MANTR<span className="text-primary">A</span>
          </h1>
          <span className="mt-1 font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground">
            Knowledge Intelligence
          </span>
        </div>
        <nav className="ml-auto flex items-center gap-1">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={cn(
                "flex h-11 items-center gap-1.5 rounded-md px-3 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:h-9",
                tab === id ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Icon className="size-4" /> <span className="hidden sm:inline">{label}</span>
            </button>
          ))}
          <Button variant="ghost" size="icon" aria-label="Toggle theme" onClick={() => setDark((d) => !d)}>
            {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </Button>
        </nav>
      </header>

      <main className="flex flex-1 flex-col py-8">
        {/* Ingest/Ask are reading-width content; Graph uses the full shell width; RCA's chain rows need a bit more room. */}
        {tab === "ingest" && <div className="mx-auto w-full max-w-2xl"><Ingest /></div>}
        {tab === "ask" && <div className="mx-auto w-full max-w-2xl"><Ask /></div>}
        {tab === "graph" && <Graph dark={dark} />}
        {tab === "rca" && <div className="mx-auto w-full max-w-3xl"><RCA /></div>}
        {tab === "compliance" && <div className="mx-auto w-full max-w-3xl"><Compliance /></div>}
        {tab === "failures" && <div className="mx-auto w-full max-w-3xl"><Failures /></div>}
        {tab === "admin" && <div className="mx-auto w-full max-w-3xl"><Admin /></div>}
      </main>

      <footer className="border-t border-border py-4 font-mono text-[11px] text-muted-foreground">
        Cited, source-grounded answers · knowledge graph · root-cause chains
      </footer>
    </div>
  );
}
