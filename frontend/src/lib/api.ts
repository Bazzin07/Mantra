// Thin fetch wrappers. Vite proxies /api -> backend and injects the API key.
// Every response is validated against a zod schema at the network boundary —
// a backend field-name mismatch throws a clear error instead of silently
// rendering blank (see ENGINEERING_AUDIT.md 2026-07-17 for the bug that motivated this).
import { z } from "zod";

const EntitySchema = z.object({ entity_type: z.string(), text: z.string(), normalized_text: z.string(), confidence: z.number() });
export type Entity = z.infer<typeof EntitySchema>;

const DocSchema = z.object({
  id: z.string(),
  status: z.string(),
  metadata: z.object({ filename: z.string(), document_type: z.string(), page_count: z.number() }),
  entities: z.array(EntitySchema),
  chunks: z.array(z.object({ id: z.string() })),
});
export type Doc = z.infer<typeof DocSchema>;

const CitationSchema = z.object({
  filename: z.string(),
  page_number: z.number(),
  relevance_score: z.number(),
  excerpt: z.string(),
});
export type Citation = z.infer<typeof CitationSchema>;

const ConfidenceSchema = z.enum(["strong", "moderate", "weak", "none"]);

const AnswerSchema = z.object({
  answer: z.string(),
  confidence: ConfidenceSchema,
  citations: z.array(CitationSchema),
  model_used: z.string().nullable(),
  generation_status: z.string(),
  query_type: z.string(),
});
export type Answer = z.infer<typeof AnswerSchema>;

const GraphSchema = z.object({
  nodes: z.array(z.object({ id: z.string(), label: z.string(), type: z.string(), metadata: z.record(z.string(), z.string()) })),
  edges: z.array(z.object({ id: z.string(), source: z.string(), target: z.string(), type: z.string(), weight: z.number() })),
});
export type Graph = z.infer<typeof GraphSchema>;

const RCALinkSchema = z.object({
  source_label: z.string(),
  target_label: z.string(),
  relationship: z.string(),
  link_confidence: z.number(),
  signals: z.record(z.string(), z.number()),
  citations: z.array(CitationSchema),
});
export type RCALink = z.infer<typeof RCALinkSchema>;

const RCAChainSchema = z.object({
  links: z.array(RCALinkSchema),
  chain_confidence: z.number(),
  confidence_label: ConfidenceSchema,
  chain_type: z.enum(["direct_similarity", "indirect_ripple", "cross_domain_impact"]),
  amplifications_applied: z.array(z.string()),
});
export type RCAChain = z.infer<typeof RCAChainSchema>;

const RCAReportSchema = z.object({
  seed: z.string(),
  chains: z.array(RCAChainSchema),
  narrative: z.string(),
  generation_status: z.string(),
  model_used: z.string().nullable(),
});
export type RCAReport = z.infer<typeof RCAReportSchema>;

const EquipmentHealthSchema = z.object({
  equipment_tag: z.string(),
  document_count: z.number(),
  document_types: z.array(z.string()),
  failure_history: z.array(z.string()),
  open_procedures: z.array(z.string()),
  related_parts: z.array(z.string()),
  timeline: z.array(z.object({ date: z.string(), event: z.string(), source_document: z.string() })),
  summary: z.string(),
});
export type EquipmentHealth = z.infer<typeof EquipmentHealthSchema>;

const MaintenancePredictionSchema = z.object({
  equipment_tag: z.string(),
  recommendation: z.string(),
  urgency: z.enum(["low", "medium", "high"]),
  justification: z.array(z.string()),
});
export type MaintenancePrediction = z.infer<typeof MaintenancePredictionSchema>;

const FailureClusterSchema = z.object({
  cluster_id: z.number(),
  failure_terms: z.array(z.string()),
  member_count: z.number(),
  representative_excerpt: z.string(),
  document_filenames: z.array(z.string()),
});
export type FailureCluster = z.infer<typeof FailureClusterSchema>;

const FailureClusterReportSchema = z.object({
  available: z.boolean(),
  reason: z.string().nullable(),
  clusters: z.array(FailureClusterSchema),
});
export type FailureClusterReport = z.infer<typeof FailureClusterReportSchema>;

const RequirementResultSchema = z.object({
  requirement_id: z.string(),
  requirement_text: z.string(),
  status: z.enum(["compliant", "partial", "gap"]),
  coverage_score: z.number(),
  citations: z.array(CitationSchema),
  action_needed: z.string(),
});
export type RequirementResult = z.infer<typeof RequirementResultSchema>;

const RegulationComplianceSchema = z.object({
  regulation: z.string(),
  title: z.string(),
  requirements: z.array(RequirementResultSchema),
  coverage_pct: z.number(),
  status_counts: z.record(z.string(), z.number()),
});
export type RegulationCompliance = z.infer<typeof RegulationComplianceSchema>;

const ComplianceStatusSchema = z.object({
  regulations: z.array(RegulationComplianceSchema),
  overall_coverage_pct: z.number(),
  framework_disclaimer: z.string(),
});
export type ComplianceStatus = z.infer<typeof ComplianceStatusSchema>;

const ComplianceGapSchema = z.object({
  regulation: z.string(),
  requirement_id: z.string(),
  requirement_text: z.string(),
  status: z.enum(["partial", "gap"]),
  evidence: z.array(CitationSchema),
  action_needed: z.string(),
});
export type ComplianceGap = z.infer<typeof ComplianceGapSchema>;

const EvidencePackageSchema = z.object({
  regulation: z.string(),
  title: z.string(),
  requirements: z.array(RequirementResultSchema),
  coverage_pct: z.number(),
  summary: z.string(),
  generation_status: z.string(),
  disclaimer: z.string(),
});
export type EvidencePackage = z.infer<typeof EvidencePackageSchema>;

const FailurePatternSchema = z.object({
  cluster_id: z.number(),
  description: z.string(),
  frequency: z.number(),
  affected_equipment: z.array(z.string()),
  severity_trend: z.enum(["escalating", "recurring", "unclassified"]),
  document_filenames: z.array(z.string()),
});
export type FailurePattern = z.infer<typeof FailurePatternSchema>;

const PatternReportSchema = z.object({
  available: z.boolean(),
  reason: z.string().nullable(),
  patterns: z.array(FailurePatternSchema),
});
export type PatternReport = z.infer<typeof PatternReportSchema>;

const SimilarIncidentSchema = z.object({
  document_id: z.string(),
  filename: z.string(),
  similarity_score: z.number(),
  lessons_learned: z.string(),
});
export type SimilarIncident = z.infer<typeof SimilarIncidentSchema>;

const SimilarIncidentReportSchema = z.object({
  seed_document_id: z.string(),
  seed_filename: z.string(),
  similar_incidents: z.array(SimilarIncidentSchema),
});
export type SimilarIncidentReport = z.infer<typeof SimilarIncidentReportSchema>;

const IncidentAnalysisSchema = z.object({
  document_id: z.string(),
  filename: z.string(),
  contributing_factors: z.array(z.string()),
  affected_equipment: z.array(z.string()),
  root_cause_summary: z.string(),
  generation_status: z.string(),
});
export type IncidentAnalysis = z.infer<typeof IncidentAnalysisSchema>;

const DocumentTypeCountSchema = z.object({ document_type: z.string(), count: z.number() });
export type DocumentTypeCount = z.infer<typeof DocumentTypeCountSchema>;

const DocumentStatsSchema = z.object({
  total_documents: z.number(),
  by_type: z.array(DocumentTypeCountSchema),
  by_status: z.record(z.string(), z.number()),
  earliest_ingested: z.string().nullable(),
  latest_ingested: z.string().nullable(),
});
export type DocumentStats = z.infer<typeof DocumentStatsSchema>;

const EndpointUsageSchema = z.object({ path: z.string(), request_count: z.number(), avg_duration_ms: z.number(), error_count: z.number() });
export type EndpointUsage = z.infer<typeof EndpointUsageSchema>;

const UsageStatsSchema = z.object({
  total_requests: z.number(),
  total_errors: z.number(),
  llm_invoking_requests: z.number(),
  by_endpoint: z.array(EndpointUsageSchema),
  note: z.string(),
});
export type UsageStats = z.infer<typeof UsageStatsSchema>;

const PipelineStatusSchema = z.object({
  documents_indexed: z.number(),
  documents_duplicate: z.number(),
  upload_errors_recent: z.number(),
  pending_reprocessing: z.number(),
  note: z.string(),
});
export type PipelineStatus = z.infer<typeof PipelineStatusSchema>;

const AdminOverviewSchema = z.object({ documents: DocumentStatsSchema, usage: UsageStatsSchema, pipeline: PipelineStatusSchema });
export type AdminOverview = z.infer<typeof AdminOverviewSchema>;

const DocumentSummarySchema = z.object({
  id: z.string(),
  filename: z.string(),
  document_type: z.string(),
  status: z.string(),
  byte_size: z.number(),
  created_at: z.string(),
});
export type DocumentSummary = z.infer<typeof DocumentSummarySchema>;

const IngestionFailureSchema = z.object({
  id: z.string(),
  filename: z.string(),
  content_type: z.string(),
  byte_size: z.number(),
  error_message: z.string(),
  attempts: z.number(),
  created_at: z.string(),
  last_attempt_at: z.string(),
});
export type IngestionFailure = z.infer<typeof IngestionFailureSchema>;

const StreamTokenFrameSchema = z.object({ type: z.literal("token"), text: z.string() });
const StreamDoneFrameSchema = z.object({
  type: z.literal("done"),
  citations: z.array(CitationSchema),
  confidence: ConfidenceSchema,
  model_used: z.string().nullable(),
  generation_status: z.string(),
  cache_status: z.string(),
});
const StreamFrameSchema = z.discriminatedUnion("type", [StreamTokenFrameSchema, StreamDoneFrameSchema]);
export type StreamFrame = z.infer<typeof StreamFrameSchema>;

// VITE_API_URL is unset in local dev (Vite's proxy handles relative /api/*
// calls and injects the key server-side, per vite.config.ts) and set to the
// deployed backend's absolute origin in production builds (e.g. Amplify),
// where there is no proxy. Either way the browser never sends an API key —
// Caddy injects it server-side in production, matching the dev-proxy pattern.
const API_BASE = import.meta.env.VITE_API_URL ?? "";
const apiUrl = (path: string) => `${API_BASE}${path}`;

// FastAPI returns `detail` as a string for HTTPExceptions but as an array of
// {loc,msg,...} objects for 422 request-validation errors. Coerce both (and any
// unexpected shape) into a readable string so the UI never shows "[object
// Object]" or an empty error.
function errorMessage(detail: unknown, r: Response): string {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    const msg = detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : JSON.stringify(d)))
      .filter(Boolean)
      .join("; ");
    if (msg) return msg;
  }
  return `${r.status} ${r.statusText}`;
}

async function json<T>(schema: z.ZodType<T>, p: Promise<Response>): Promise<T> {
  const r = await p;
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(errorMessage((body as { detail?: unknown }).detail, r));
  }
  const parsed = schema.safeParse(await r.json());
  if (!parsed.success) {
    console.error("API response failed validation:", parsed.error.issues);
    throw new Error(`Unexpected response shape from server: ${parsed.error.issues[0]?.path.join(".")} — ${parsed.error.issues[0]?.message}`);
  }
  return parsed.data;
}

export const uploadDoc = (file: File) => {
  const fd = new FormData();
  fd.append("file", file);
  return json(DocSchema, fetch(apiUrl("/api/documents/upload"), { method: "POST", body: fd }));
};

export const ask = (question: string) =>
  json(
    AnswerSchema,
    fetch(apiUrl("/api/copilot/ask"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, limit: 4 }),
    }),
  );

export const getGraph = () => json(GraphSchema, fetch(apiUrl("/api/knowledge-graph")));
export const health = () => fetch(apiUrl("/api/health")).then((r) => r.json());

export const getRCA = (equipmentTag: string) =>
  json(RCAReportSchema, fetch(apiUrl(`/api/maintenance/rca/${encodeURIComponent(equipmentTag)}`), { method: "POST" }));
export const getEquipmentHealth = (equipmentTag: string) =>
  json(EquipmentHealthSchema, fetch(apiUrl(`/api/maintenance/health/${encodeURIComponent(equipmentTag)}`)));
export const getPredictions = () => json(z.array(MaintenancePredictionSchema), fetch(apiUrl("/api/maintenance/predictions")));
export const getClusters = () => json(FailureClusterReportSchema, fetch(apiUrl("/api/maintenance/clusters")));
export const investigate = (question: string) =>
  json(
    RCAReportSchema,
    fetch(apiUrl("/api/maintenance/investigate"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, limit: 4 }),
    }),
  );

export const getComplianceStatus = () => json(ComplianceStatusSchema, fetch(apiUrl("/api/compliance/status")));
export const getComplianceGaps = () => json(z.array(ComplianceGapSchema), fetch(apiUrl("/api/compliance/gaps")));
export const getAuditPackage = (regulation: string) =>
  json(EvidencePackageSchema, fetch(apiUrl(`/api/compliance/audit/${encodeURIComponent(regulation)}`), { method: "POST" }));

export const getAdminOverview = () => json(AdminOverviewSchema, fetch(apiUrl("/api/admin/overview")));
export const getDocuments = (documentType?: string, equipmentTag?: string) => {
  const params = new URLSearchParams();
  if (documentType) params.set("document_type", documentType);
  if (equipmentTag) params.set("equipment_tag", equipmentTag);
  const qs = params.toString();
  return json(z.array(DocumentSummarySchema), fetch(apiUrl(`/api/documents${qs ? `?${qs}` : ""}`)));
};

export const getIngestionFailures = () => json(z.array(IngestionFailureSchema), fetch(apiUrl("/api/admin/ingestion-failures")));
export const reprocessIngestionFailure = (failureId: string) =>
  json(DocSchema, fetch(apiUrl(`/api/admin/ingestion-failures/${encodeURIComponent(failureId)}/reprocess`), { method: "POST" }));
export const dismissIngestionFailure = (failureId: string) =>
  fetch(apiUrl(`/api/admin/ingestion-failures/${encodeURIComponent(failureId)}`), { method: "DELETE" }).then((r) => {
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  });

export async function* streamAsk(question: string): AsyncGenerator<StreamFrame> {
  const response = await fetch(apiUrl("/api/copilot/stream"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, limit: 4 }),
  });
  if (!response.ok || !response.body) throw new Error(`${response.status} ${response.statusText}`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith("data:")) continue;
      yield StreamFrameSchema.parse(JSON.parse(line.slice(5).trim()));
    }
  }
}

export const getFailurePatterns = () => json(PatternReportSchema, fetch(apiUrl("/api/failures/patterns")));
export const getSimilarIncidents = (documentId: string) =>
  json(SimilarIncidentReportSchema, fetch(apiUrl(`/api/failures/similar/${encodeURIComponent(documentId)}`)));
export const getIncidentAnalysis = (documentId: string) =>
  json(IncidentAnalysisSchema, fetch(apiUrl(`/api/failures/analysis/${encodeURIComponent(documentId)}`)));
