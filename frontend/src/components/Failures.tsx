import { useEffect, useMemo, useState } from "react";
import { Activity, FileText, Loader2 } from "lucide-react";
import { Button } from "./ui/button";
import { cn } from "../lib/utils";
import {
  getFailurePatterns,
  getSimilarIncidents,
  getIncidentAnalysis,
  getGraph,
  type PatternReport,
  type SimilarIncidentReport,
  type IncidentAnalysis,
} from "../lib/api";

const TREND_BADGE: Record<string, string> = {
  escalating: "border-destructive/40 text-destructive",
  recurring: "border-primary/30 text-primary/80",
  unclassified: "border-border text-muted-foreground",
};

export function Failures() {
  const [patterns, setPatterns] = useState<PatternReport | null>(null);
  const [documents, setDocuments] = useState<{ id: string; label: string }[]>([]);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [similar, setSimilar] = useState<SimilarIncidentReport | null>(null);
  const [analysis, setAnalysis] = useState<IncidentAnalysis | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getFailurePatterns().then(setPatterns).catch((e) => setError((e as Error).message));
    getGraph()
      .then((g) =>
        setDocuments(
          g.nodes
            .filter((n) => n.type === "DOCUMENT")
            .map((n) => ({ id: n.id.replace("document:", ""), label: n.label }))
            .sort((a, b) => a.label.localeCompare(b.label)),
        ),
      )
      .catch(() => setDocuments([]));
  }, []);

  const investigate = async (documentId: string) => {
    if (!documentId) return;
    setBusy(true);
    setError(null);
    setSimilar(null);
    setAnalysis(null);
    try {
      const [similarReport, analysisReport] = await Promise.all([
        getSimilarIncidents(documentId),
        getIncidentAnalysis(documentId),
      ]);
      setSimilar(similarReport);
      setAnalysis(analysisReport);
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  };

  const selectedLabel = useMemo(() => documents.find((d) => d.id === selected)?.label, [documents, selected]);

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
          Systemic failure patterns
        </span>
        {!patterns ? (
          <div className="grid place-items-center py-8"><Loader2 className="size-5 animate-spin text-primary" /></div>
        ) : !patterns.available ? (
          <p className="text-sm text-muted-foreground">{patterns.reason}</p>
        ) : patterns.patterns.length === 0 ? (
          <p className="text-sm text-muted-foreground">No recurring failure patterns found yet.</p>
        ) : (
          <div className="flex flex-col gap-3">
            {patterns.patterns.map((p) => (
              <div key={p.cluster_id} className="flex flex-col gap-2 rounded-lg border border-border p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Activity className="size-3.5 shrink-0 text-primary" />
                  <span className="text-sm font-medium">{p.description}</span>
                  <span className={cn("ml-auto rounded-sm border px-2 py-0.5 font-mono text-[11px] uppercase", TREND_BADGE[p.severity_trend])}>
                    {p.severity_trend}
                  </span>
                </div>
                <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
                  <span className="tabular-nums">{p.frequency} occurrences</span>
                  {p.affected_equipment.length > 0 && <span>{p.affected_equipment.join(", ")}</span>}
                </div>
                <p className="font-mono text-[11px] text-muted-foreground">{p.document_filenames.join(", ")}</p>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="flex flex-col gap-3">
        <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
          Similar-incident search
        </span>
        <div className="flex gap-2">
          <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            className="w-full rounded-lg border border-input bg-card px-3 py-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="">Select a document…</option>
            {documents.map((d) => (
              <option key={d.id} value={d.id}>{d.label}</option>
            ))}
          </select>
          <Button onClick={() => investigate(selected)} disabled={busy || !selected}>
            {busy && <Loader2 className="size-4 animate-spin" />} Search
          </Button>
        </div>

        {error && <div className="rounded-md border border-destructive/40 px-3 py-2 text-sm text-destructive">{error}</div>}

        {analysis && (
          <div className="flex flex-col gap-2 rounded-lg border border-border p-3 animate-fade-up">
            <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
              Incident analysis — {analysis.filename}
            </span>
            {analysis.contributing_factors.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {analysis.contributing_factors.map((f) => (
                  <span key={f} className="rounded-sm border border-destructive/30 px-2 py-0.5 font-mono text-[11px] text-destructive/90">
                    {f}
                  </span>
                ))}
                {analysis.affected_equipment.map((eq) => (
                  <span key={eq} className="rounded-sm border border-primary/30 px-2 py-0.5 font-mono text-[11px] text-primary/80">
                    {eq}
                  </span>
                ))}
              </div>
            )}
            <p className="text-sm leading-relaxed">{analysis.root_cause_summary}</p>
          </div>
        )}

        {similar && (
          <div className="flex flex-col gap-2 animate-fade-up">
            <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
              Most similar to {selectedLabel ?? similar.seed_filename}
            </span>
            <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
              {similar.similar_incidents.map((s) => (
                <li key={s.document_id} className="flex flex-col gap-1 p-3">
                  <div className="flex items-center gap-2 text-sm">
                    <FileText className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate font-mono text-xs">{s.filename}</span>
                    <span className="ml-auto shrink-0 font-mono text-xs tabular-nums text-primary">
                      {s.similarity_score.toFixed(3)}
                    </span>
                  </div>
                  <p className="line-clamp-2 pl-5 text-xs text-muted-foreground">{s.lessons_learned}</p>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
