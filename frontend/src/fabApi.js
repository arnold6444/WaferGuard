export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export async function fetchJson(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, { cache: "no-store", ...options });
  if (!response.ok) {
    const error = new Error(`HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

export function rowsFrom(payload, keys = []) {
  if (Array.isArray(payload)) return payload;
  for (const key of keys) {
    if (Array.isArray(payload?.[key])) return payload[key];
  }
  return [];
}

export function buildQuery(values) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(values || {})) {
    if (value != null && value !== "" && value !== "all") query.set(key, value);
  }
  const encoded = query.toString();
  return encoded ? `?${encoded}` : "";
}

export function normalizeEquipment(payload) {
  const direct = rowsFrom(payload, ["items", "equipment", "equipment_units", "units"]);
  const nested = rowsFrom(payload, ["processes"]).flatMap(process => {
    const rows = rowsFrom(process, ["items", "equipment", "equipment_units", "units"]);
    return rows.map(item => ({ process_id: process.process_id, ...item }));
  });
  return [...direct, ...nested].map(item => ({
    ...item,
    process_id: String(item.process_id || item.process_step || "").toLowerCase(),
    equipment_id: item.equipment_id || item.tool_id || "",
    unit_id: item.unit_id || item.chamber_id || "",
    recipe_id: item.recipe_id || "",
    machine_state: String(item.machine_state || item.state || item.status || "unknown").toLowerCase(),
  }));
}

export function normalizeProcessRunRows(payload) {
  return rowsFrom(payload, ["process_runs", "route", "runs", "items"])
    .map(item => ({
      ...item,
      process_run_id: item.process_run_id || item.id,
      process_id: String(item.process_id || item.process_step || "").toLowerCase(),
      process_step: item.process_step || item.display_name || item.process_id,
      started_at: item.started_at || item.start_at || item.observed_at,
      ended_at: item.ended_at || item.end_at || item.completed_at,
    }))
    .filter(item => item.process_run_id);
}
