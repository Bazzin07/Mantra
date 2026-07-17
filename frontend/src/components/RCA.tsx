import { useEffect, useState } from "react";
import { ArrowRight, Loader2, AlertTriangle, Clock, CornerDownLeft } from "lucide-react";
import { useRCA } from "../hooks/useRCA";
import { Button } from "./ui/button";
import { cn, CONFIDENCE_BADGE } from "../lib/utils";
import { getPredictions, getClusters, type RCAChain, type MaintenancePrediction, type FailureClusterReport } from "../lib/api";

const SAMPLES = ["P-101A", "C-501", "HX-2042", "CH-12"];

const CHAIN_TYPE_LABEL: Record<RCAChain["chain_type"], string> = {
  direct_similarity: "Direct",
  indirect_ripple: "Indirect",
  cross_domain_impact: "Cross-system",
};

const AMPLIFICATION_LABEL: Record<string, string> = {
  rare_failure_mode_boost: "Rare failure",
  cross_system_boost: "Cross-system",
};

const URGENCY_BADGE: Record<MaintenancePrediction["urgency"], string> = {
  high: "border-destructive/40 text-destructive",
  medium: "border-primary/30 text-primary/80",
  low: "border-border text-muted-foreground",
};

export function RCA() {
  const [tag, setTag] = useState("");
  const [incident, setIncident] = useState("");
  const { busy, rca, equipmentHealth, error, run, runFreeText } = useRCA();
  const [predictions, setPredictions] = useState<MaintenancePrediction[] | null>(null);
  const [clusters, setClusters] = useState<FailureClusterReport | null>(null);

  useEffect(() => {
    getPredictions().then(setPredictions).catch(() => setPredictions([]));
    getClusters().then(setClusters).catch(() => setClusters(null));
  }, []);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-3">
        <div className="flex gap-2">
          <input
            value={tag}
            onChange={(e) => setTag(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") run(tag); }}
            placeholder="Equipment tag, e.g. P-101A…"
            className="w-full rounded-lg border border-input bg-card px-4 py-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring md:text-sm"
          />
          <Button onClick={() => run(tag)} disabled={busy || !tag.trim()}>
            {busy && <Loader2 className="size-4 animate-spin" />} Investigate
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {SAMPLES.map((s) => (
            <Button key={s} variant="outline" size="sm" onClick={() => { setTag(s); run(s); }} disabled={busy}>
              {s}
            </Button>
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
          Or describe an incident
        </span>
        <div className="relative">
          <textarea
            value={incident}
            onChange={(e) => setIncident(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) runFreeText(incident); }}
            rows={2}
            placeholder="e.g. Compressor tripped on high discharge temperature after startup…"
            className="w-full resize-none rounded-lg border border-input bg-card px-4 py-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring md:text-sm"
          />
          <span className="pointer-events-none absolute bottom-3 right-3 hidden items-center gap-1 font-mono text-[10px] text-muted-foreground sm:flex">
            <CornerDownLeft className="size-3" />⌘↵
          </span>
        </div>
        <Button variant="outline" size="sm" onClick={() => runFreeText(incident)} disabled={busy || !incident.trim()} className="self-start">
          {busy && <Loader2 className="size-4 animate-spin" />} Investigate incident
        </Button>
      </div>

      {error && <div className="rounded-md border border-destructive/40 px-3 py-2 text-sm text-destructive">{error}</div>}

      {busy && !rca && <InvestigatingSkeleton />}

      {equipmentHealth && equipmentHealth.document_count > 0 && (
        <div className="flex flex-col gap-3 animate-fade-up">
          <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
            Equipment health — {equipmentHealth.equipment_tag}
          </span>
          <p className="text-[15px] leading-relaxed">{equipmentHealth.summary}</p>
          <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
            <span className="tabular-nums">{equipmentHealth.document_count} documents</span>
            <span>{equipmentHealth.document_types.join(", ")}</span>
          </div>
          {equipmentHealth.failure_history.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {equipmentHealth.failure_history.map((term) => (
                <span key={term} className="rounded-sm border border-destructive/30 px-2 py-0.5 font-mono text-[11px] text-destructive/90">
                  {term}
                </span>
              ))}
            </div>
          )}
          {equipmentHealth.timeline.length > 0 && (
            <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
              {equipmentHealth.timeline.map((row, i) => (
                <li key={i} className="flex items-center gap-2 p-2.5 text-xs">
                  <Clock className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="font-mono tabular-nums text-muted-foreground">{row.date}</span>
                  <span className="truncate">{row.source_document}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {rca && (
        <div className="flex flex-col gap-4 animate-fade-up">
          <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
            Root cause chains
          </span>
          {rca.chains.length === 0 ? (
            <p className="text-sm text-muted-foreground">{rca.narrative}</p>
          ) : (
            <>
              <p className="text-[15px] leading-relaxed">{rca.narrative}</p>
              <div className="flex flex-col gap-3">
                {rca.chains.map((chain, i) => (
                  <ChainCard key={i} chain={chain} />
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {predictions && predictions.length > 0 && (
        <div className="flex flex-col gap-3">
          <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
            Recommended maintenance
          </span>
          <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {predictions.map((p) => (
              <li key={p.equipment_tag} className="flex flex-col gap-1.5 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={cn("rounded-sm border px-2 py-0.5 font-mono text-[11px] uppercase", URGENCY_BADGE[p.urgency])}>
                    {p.urgency}
                  </span>
                  <span className="font-mono text-xs">{p.equipment_tag}</span>
                </div>
                <p className="text-sm">{p.recommendation}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {clusters && (
        <div className="flex flex-col gap-3">
          <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
            Failure pattern clusters
          </span>
          {!clusters.available ? (
            <p className="text-sm text-muted-foreground">{clusters.reason}</p>
          ) : clusters.clusters.length === 0 ? (
            <p className="text-sm text-muted-foreground">No recurring failure clusters found yet.</p>
          ) : (
            <div className="flex flex-col gap-3">
              {clusters.clusters.map((c) => (
                <div key={c.cluster_id} className="flex flex-col gap-2 rounded-lg border border-border p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-sm border border-primary/30 px-2 py-0.5 font-mono text-[11px] text-primary/80">
                      {c.member_count} member{c.member_count === 1 ? "" : "s"}
                    </span>
                    {c.failure_terms.map((term) => (
                      <span key={term} className="rounded-sm border border-destructive/30 px-2 py-0.5 font-mono text-[11px] text-destructive/90">
                        {term}
                      </span>
                    ))}
                  </div>
                  <p className="line-clamp-2 text-xs text-muted-foreground">{c.representative_excerpt}</p>
                  <p className="font-mono text-[11px] text-muted-foreground">{c.document_filenames.join(", ")}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function InvestigatingSkeleton() {
  return (
    <div className="flex flex-col gap-4 animate-fade-up" role="status" aria-live="polite">
      <div className="flex flex-col gap-1.5">
        <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
          Investigating — the model can take up to a minute reasoning over the evidence
        </span>
        <div className="h-px w-full overflow-hidden bg-border">
          <div className="h-full w-1/4 bg-primary" style={{ animation: "bar-sweep 1.1s ease-in-out infinite" }} />
        </div>
      </div>
      <div className="flex flex-col gap-2">
        <div className="h-4 w-2/3 animate-pulse rounded bg-muted" />
        <div className="h-4 w-1/2 animate-pulse rounded bg-muted" />
      </div>
      {[0, 1].map((i) => (
        <div key={i} className="flex flex-col gap-2 rounded-lg border border-border p-3">
          <div className="flex gap-2">
            <div className="h-5 w-20 animate-pulse rounded bg-muted" />
            <div className="h-5 w-24 animate-pulse rounded bg-muted" />
          </div>
          <div className="h-4 w-4/5 animate-pulse rounded bg-muted" />
          <div className="h-4 w-3/5 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

function ChainCard({ chain }: { chain: RCAChain }) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn("rounded-sm border px-2 py-0.5 font-mono text-[11px] uppercase", CONFIDENCE_BADGE[chain.confidence_label])}>
          {chain.confidence_label} · {chain.chain_confidence.toFixed(2)}
        </span>
        <span className="rounded-sm border border-border px-2 py-0.5 font-mono text-[11px] uppercase text-muted-foreground">
          {CHAIN_TYPE_LABEL[chain.chain_type]}
        </span>
        {chain.amplifications_applied.map((a) => (
          <span key={a} className="flex items-center gap-1 rounded-sm border border-primary/30 px-2 py-0.5 font-mono text-[11px] text-primary/80">
            <AlertTriangle className="size-3" /> {AMPLIFICATION_LABEL[a] ?? a}
          </span>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-sm">
        <span className="font-medium">{chain.links[0]?.source_label}</span>
        {chain.links.map((link, i) => (
          <span key={i} className="flex items-center gap-1.5">
            <ArrowRight className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="font-medium">{link.target_label}</span>
          </span>
        ))}
      </div>

      <div className="flex flex-col gap-1 pl-1 text-xs text-muted-foreground">
        {chain.links.map((link, i) => (
          <div key={i} className="flex items-center gap-2">
            <span className="truncate">
              {link.source_label} → {link.target_label} ({link.relationship})
            </span>
            <span className="ml-auto shrink-0 font-mono tabular-nums text-primary">{link.link_confidence.toFixed(2)}</span>
            <span className="shrink-0 tabular-nums">{link.citations.length} cite{link.citations.length === 1 ? "" : "s"}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
