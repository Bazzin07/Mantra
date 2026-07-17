import { FileText } from "lucide-react";
import { type Answer } from "../lib/api";
import { cn, CONFIDENCE_BADGE } from "../lib/utils";

export function AnswerView({ res, compact, streaming }: { res: Answer; compact?: boolean; streaming?: boolean }) {
  return (
    <div className="flex flex-col gap-4 animate-fade-up">
      {!streaming && (
        <div className="flex flex-wrap items-center gap-2">
          <span className={cn("rounded-sm border px-2 py-0.5 font-mono text-[11px] uppercase", CONFIDENCE_BADGE[res.confidence])}>
            {res.confidence} confidence
          </span>
          {res.model_used && (
            <span className="truncate font-mono text-[11px] text-muted-foreground">{res.model_used}</span>
          )}
        </div>
      )}

      <p className={cn("leading-relaxed", compact ? "text-sm" : "text-[15px]")}>
        {res.answer}
        {streaming && <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-primary align-middle" />}
      </p>

      {!streaming && res.citations.length > 0 && (
        <div className="flex flex-col gap-2">
          <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">Sources</span>
          <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {res.citations.map((c, i) => (
              <li key={i} className="flex flex-col gap-1 p-3">
                <div className="flex items-center gap-2 text-sm">
                  <FileText className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="truncate font-mono text-xs">{c.filename}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">p.{c.page_number}</span>
                  <span className="ml-auto shrink-0 font-mono text-xs tabular-nums text-primary">
                    {c.relevance_score.toFixed(2)}
                  </span>
                </div>
                <p className="line-clamp-2 pl-5 text-xs text-muted-foreground">{c.excerpt}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
