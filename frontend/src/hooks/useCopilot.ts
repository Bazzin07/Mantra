import { useCallback, useState } from "react";
import { ask, streamAsk, type Answer } from "../lib/api";

const EMPTY_ANSWER: Answer = {
  answer: "",
  confidence: "weak",
  citations: [],
  model_used: null,
  generation_status: "streaming",
  query_type: "simple_evidence_query",
};

export function useCopilot() {
  const [busy, setBusy] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [res, setRes] = useState<Answer | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async (question: string) => {
    const text = question.trim();
    if (!text) return;
    setBusy(true);
    setError(null);
    setRes(null);
    try {
      setRes(await ask(text));
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  }, []);

  const runStream = useCallback(async (question: string) => {
    const text = question.trim();
    if (!text) return;
    setBusy(true);
    setStreaming(true);
    setError(null);
    setRes(null);
    let answer = "";
    try {
      for await (const frame of streamAsk(text)) {
        if (frame.type === "token") {
          answer += frame.text;
          setRes({ ...EMPTY_ANSWER, answer });
        } else {
          setRes({
            answer,
            confidence: frame.confidence,
            citations: frame.citations,
            model_used: frame.model_used,
            generation_status: frame.generation_status,
            query_type: "simple_evidence_query",
          });
        }
      }
    } catch (e) {
      setError((e as Error).message);
    }
    setStreaming(false);
    setBusy(false);
  }, []);

  return { busy, streaming, res, error, run, runStream };
}
