import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export const cn = (...inputs: ClassValue[]) => twMerge(clsx(inputs));

// Shared confidence-badge styling: same strong/moderate/weak/none scale used
// by both the copilot answer and RCA chain confidence.
export const CONFIDENCE_BADGE: Record<string, string> = {
  strong: "border-primary/40 text-primary",
  moderate: "border-primary/30 text-primary/80",
  weak: "border-border text-muted-foreground",
  none: "border-border text-muted-foreground",
};
