import { useState } from "react";
import { Loader2, CornerDownLeft } from "lucide-react";
import { useCopilot } from "../hooks/useCopilot";
import { AnswerView } from "./AnswerView";
import { Button } from "./ui/button";

const SAMPLES = [
  "What happened to P-101A?",
  "What is the maintenance interval for P-101A?",
  "What caused the Piper Alpha explosion?",
];

export function Ask() {
  const [q, setQ] = useState("");
  const { busy, streaming, res, error, runStream } = useCopilot();

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-3">
        <div className="relative">
          <textarea
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) runStream(q); }}
            rows={3}
            placeholder="Ask about equipment, failures, procedures, incidents…"
            className="w-full resize-none rounded-lg border border-input bg-card px-4 py-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring md:text-sm"
          />
          <span className="pointer-events-none absolute bottom-3 right-3 hidden items-center gap-1 font-mono text-[10px] text-muted-foreground sm:flex">
            <CornerDownLeft className="size-3" />⌘↵
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => runStream(q)} disabled={busy || !q.trim()}>
            {busy && <Loader2 className="size-4 animate-spin" />} Ask
          </Button>
          {SAMPLES.map((s) => (
            <Button key={s} variant="outline" size="sm" onClick={() => { setQ(s); runStream(s); }} disabled={busy}>
              {s}
            </Button>
          ))}
        </div>
      </div>

      {error && <div className="rounded-md border border-destructive/40 px-3 py-2 text-sm text-destructive">{error}</div>}
      {res && <AnswerView res={res} streaming={streaming} />}
    </div>
  );
}
