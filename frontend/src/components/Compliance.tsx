import { useEffect, useState } from "react";
import { FileText, Loader2, ShieldAlert } from "lucide-react";
import { Button } from "./ui/button";
import { cn } from "../lib/utils";
import {
  getComplianceStatus,
  getComplianceGaps,
  getAuditPackage,
  type ComplianceStatus,
  type ComplianceGap,
  type EvidencePackage,
} from "../lib/api";

const STATUS_BADGE: Record<string, string> = {
  compliant: "border-primary/40 text-primary",
  partial: "border-primary/25 text-primary/70",
  gap: "border-destructive/40 text-destructive",
};

export function Compliance() {
  const [status, setStatus] = useState<ComplianceStatus | null>(null);
  const [gaps, setGaps] = useState<ComplianceGap[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [auditFor, setAuditFor] = useState<string | null>(null);
  const [audit, setAudit] = useState<EvidencePackage | null>(null);
  const [auditBusy, setAuditBusy] = useState(false);

  useEffect(() => {
    getComplianceStatus().then(setStatus).catch((e) => setError((e as Error).message));
    getComplianceGaps().then(setGaps).catch(() => setGaps([]));
  }, []);

  const runAudit = async (regulation: string) => {
    setAuditFor(regulation);
    setAudit(null);
    setAuditBusy(true);
    try {
      setAudit(await getAuditPackage(regulation));
    } catch (e) {
      setError((e as Error).message);
    }
    setAuditBusy(false);
  };

  if (error && !status) {
    return <div className="rounded-md border border-destructive/40 px-3 py-2 text-sm text-destructive">{error}</div>;
  }
  if (!status) {
    return <div className="grid place-items-center py-16"><Loader2 className="size-6 animate-spin text-primary" /></div>;
  }

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">Overall coverage</span>
          <span className="font-mono text-2xl tabular-nums text-primary">{status.overall_coverage_pct.toFixed(1)}%</span>
        </div>
        <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
          {status.regulations.map((reg) => (
            <div key={reg.regulation} className="flex flex-col gap-2 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm">{reg.regulation}</span>
                <span className="truncate text-xs text-muted-foreground">{reg.title}</span>
                <span className="ml-auto font-mono text-sm tabular-nums">{reg.coverage_pct.toFixed(1)}%</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-primary" style={{ width: `${reg.coverage_pct}%` }} />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {(["compliant", "partial", "gap"] as const).map((s) =>
                  reg.status_counts[s] ? (
                    <span key={s} className={cn("rounded-sm border px-2 py-0.5 font-mono text-[11px] uppercase", STATUS_BADGE[s])}>
                      {reg.status_counts[s]} {s}
                    </span>
                  ) : null,
                )}
                <Button variant="outline" size="sm" className="ml-auto" onClick={() => runAudit(reg.regulation)} disabled={auditBusy}>
                  {auditBusy && auditFor === reg.regulation && <Loader2 className="size-3.5 animate-spin" />}
                  Evidence package
                </Button>
              </div>
            </div>
          ))}
        </div>
        <p className="text-[11px] leading-relaxed text-muted-foreground">{status.framework_disclaimer}</p>
      </div>

      {audit && (
        <div className="flex flex-col gap-3 animate-fade-up">
          <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
            Evidence package — {audit.regulation}
          </span>
          <p className="text-[15px] leading-relaxed">{audit.summary}</p>
          <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {audit.requirements.map((req) => (
              <div key={req.requirement_id} className="flex flex-col gap-1.5 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={cn("rounded-sm border px-2 py-0.5 font-mono text-[11px] uppercase", STATUS_BADGE[req.status])}>
                    {req.status}
                  </span>
                  <span className="font-mono text-xs text-muted-foreground">{req.requirement_id}</span>
                  <span className="ml-auto font-mono text-xs tabular-nums text-primary">{req.coverage_score.toFixed(2)}</span>
                </div>
                <p className="text-sm">{req.requirement_text}</p>
                {req.citations.map((c, i) => (
                  <div key={i} className="flex items-center gap-2 pl-1 text-xs text-muted-foreground">
                    <FileText className="size-3 shrink-0" />
                    <span className="truncate font-mono">{c.filename}</span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}

      {gaps && gaps.length > 0 && (
        <div className="flex flex-col gap-3">
          <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
            Open gaps ({gaps.length})
          </span>
          <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {gaps.map((gap) => (
              <li key={`${gap.regulation}-${gap.requirement_id}`} className="flex flex-col gap-1.5 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <ShieldAlert className={cn("size-3.5 shrink-0", gap.status === "gap" ? "text-destructive" : "text-primary/70")} />
                  <span className={cn("rounded-sm border px-2 py-0.5 font-mono text-[11px] uppercase", STATUS_BADGE[gap.status])}>
                    {gap.status}
                  </span>
                  <span className="font-mono text-xs">{gap.regulation}</span>
                  <span className="font-mono text-xs text-muted-foreground">{gap.requirement_id}</span>
                </div>
                <p className="text-sm">{gap.requirement_text}</p>
                <p className="text-xs text-muted-foreground">{gap.action_needed}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
