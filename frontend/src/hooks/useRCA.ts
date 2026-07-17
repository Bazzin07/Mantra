import { useCallback, useState } from "react";
import { getRCA, getEquipmentHealth, investigate, type RCAReport, type EquipmentHealth } from "../lib/api";

export function useRCA() {
  const [busy, setBusy] = useState(false);
  const [rca, setRCA] = useState<RCAReport | null>(null);
  const [equipmentHealth, setEquipmentHealth] = useState<EquipmentHealth | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async (equipmentTag: string) => {
    const tag = equipmentTag.trim();
    if (!tag) return;
    setBusy(true);
    setError(null);
    setRCA(null);
    setEquipmentHealth(null);
    try {
      const [rcaReport, healthReport] = await Promise.all([getRCA(tag), getEquipmentHealth(tag)]);
      setRCA(rcaReport);
      setEquipmentHealth(healthReport);
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  }, []);

  const runFreeText = useCallback(async (incidentText: string) => {
    const text = incidentText.trim();
    if (!text) return;
    setBusy(true);
    setError(null);
    setRCA(null);
    setEquipmentHealth(null);
    try {
      const rcaReport = await investigate(text);
      setRCA(rcaReport);
      if (rcaReport.chains.length > 0) setEquipmentHealth(await getEquipmentHealth(rcaReport.seed));
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  }, []);

  return { busy, rca, equipmentHealth, error, run, runFreeText };
}
