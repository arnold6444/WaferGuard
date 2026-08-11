"""Stateful validation gate for Chamber telemetry before model inference."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any


VALID = "VALID"
WARNING = "WARNING"
REJECT = "REJECT"


@dataclass(frozen=True)
class DataQualityResult:
    status: str
    issues: list[dict[str, Any]]
    aggregate_events: list[dict[str, Any]]
    persistable: bool


class ChamberDataQualityGate:
    """Validate ordering, sampling, values and categorical contracts per tool."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        quality = config.get("data_quality", {})
        self.required_fields = tuple(quality.get("required_fields", ()))
        self.expected_interval = float(
            quality.get("expected_interval_seconds", config.get("stream", {}).get("sample_interval_seconds", 1.0))
        )
        self.interval_tolerance = float(quality.get("interval_tolerance_seconds", max(0.25, self.expected_interval * 0.5)))
        self.gap_warning = float(quality.get("gap_warning_seconds", max(3.0, self.expected_interval * 3.0)))
        self.stuck_limit = max(2, int(quality.get("stuck_consecutive_samples", 5)))
        self.stuck_tolerance = max(0.0, float(quality.get("stuck_tolerance", 1e-9)))
        self.aggregate_after = max(1, int(quality.get("aggregate_event_after", 2)))
        self.ranges = quality.get("physical_ranges", {})
        self.stuck_sensors = tuple(quality.get("stuck_sensors", ("chamber_pressure", "chamber_temperature")))
        self.valid_states = set(quality.get("valid_states", ())) or {
            "idle", "startup", "running", "hold", "alarm", "cleaning", "maintenance", "shutdown"
        }
        self.valid_recipes = set(config.get("recipes", {}))
        self._last_timestamp: dict[str, datetime] = {}
        self._last_signature: dict[str, str] = {}
        self._last_values: dict[tuple[str, str], float] = {}
        self._stuck_counts: dict[tuple[str, str], int] = {}
        self._issue_counts: dict[tuple[str, str], int] = {}

    def validate(self, sample: dict[str, Any]) -> DataQualityResult:
        equipment_id = str(sample.get("equipment_id") or "UNKNOWN")
        issues: list[dict[str, Any]] = []
        missing = [field for field in self.required_fields if sample.get(field) is None]
        if missing:
            issues.append(self._issue("missing_value", REJECT, f"Missing required fields: {', '.join(missing)}"))

        state = str(sample.get("machine_state") or "")
        if state not in self.valid_states:
            issues.append(self._issue("invalid_state", REJECT, f"Unsupported machine_state={state!r}"))
        recipe = str(sample.get("recipe_id") or "")
        if recipe not in self.valid_recipes:
            issues.append(self._issue("invalid_recipe", REJECT, f"Unknown recipe_id={recipe!r}"))

        timestamp = self._parse_timestamp(sample.get("observed_at"), issues)
        signature = self._signature(sample)
        previous_time = self._last_timestamp.get(equipment_id)
        if timestamp and previous_time:
            delta = (timestamp - previous_time).total_seconds()
            if delta < 0:
                issues.append(self._issue("timestamp_reversal", REJECT, f"Timestamp moved backwards by {abs(delta):.3f}s"))
            elif delta == 0 and signature == self._last_signature.get(equipment_id):
                issues.append(self._issue("duplicate_sample", REJECT, "Same equipment, timestamp and payload were repeated"))
            elif delta > self.gap_warning:
                issues.append(self._issue("timestamp_gap", WARNING, f"Sampling gap {delta:.3f}s exceeds {self.gap_warning:.3f}s"))
            elif abs(delta - self.expected_interval) > self.interval_tolerance:
                issues.append(
                    self._issue(
                        "sampling_interval_anomaly",
                        WARNING,
                        f"Sampling interval {delta:.3f}s differs from expected {self.expected_interval:.3f}s",
                    )
                )

        for field, limits in self.ranges.items():
            value = sample.get(field)
            if value is None:
                continue
            try:
                numeric = float(value)
                lower, upper = float(limits[0]), float(limits[1])
            except (TypeError, ValueError, IndexError):
                issues.append(self._issue("invalid_numeric", REJECT, f"{field} is not numeric"))
                continue
            if not lower <= numeric <= upper:
                issues.append(
                    self._issue(
                        "impossible_physical_range",
                        REJECT,
                        f"{field}={numeric:.6g} outside [{lower:.6g}, {upper:.6g}]",
                        field=field,
                    )
                )

        if state == "running":
            for sensor in self.stuck_sensors:
                value = sample.get(sensor)
                if value is None:
                    continue
                key = (equipment_id, sensor)
                numeric = float(value)
                previous = self._last_values.get(key)
                if previous is not None and abs(numeric - previous) <= self.stuck_tolerance:
                    self._stuck_counts[key] = self._stuck_counts.get(key, 1) + 1
                else:
                    self._stuck_counts[key] = 1
                self._last_values[key] = numeric
                if self._stuck_counts[key] >= self.stuck_limit:
                    issues.append(
                        self._issue(
                            "stuck_sensor",
                            WARNING,
                            f"{sensor} unchanged for {self._stuck_counts[key]} running samples",
                            field=sensor,
                        )
                    )
        else:
            for sensor in self.stuck_sensors:
                self._stuck_counts.pop((equipment_id, sensor), None)

        issue_ids = {self._issue_identity(issue) for issue in issues}
        aggregate_events: list[dict[str, Any]] = []
        known_issue_ids = {issue_id for tool, issue_id in self._issue_counts if tool == equipment_id}
        for issue_id in known_issue_ids - issue_ids:
            self._issue_counts.pop((equipment_id, issue_id), None)
        for issue in issues:
            key = (equipment_id, self._issue_identity(issue))
            self._issue_counts[key] = self._issue_counts.get(key, 0) + 1
            if self._issue_counts[key] == self.aggregate_after:
                aggregate_events.append({**issue, "count": self._issue_counts[key]})

        has_reject = any(issue["severity"] == REJECT for issue in issues)
        status = REJECT if has_reject else (WARNING if issues else VALID)
        persistable = not missing
        if timestamp and not any(issue["code"] in {"timestamp_reversal", "duplicate_sample"} for issue in issues):
            self._last_timestamp[equipment_id] = timestamp
            self._last_signature[equipment_id] = signature
        return DataQualityResult(status, issues, aggregate_events, persistable)

    @staticmethod
    def _issue(code: str, severity: str, detail: str, *, field: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {"code": code, "severity": severity, "detail": detail}
        if field:
            result["field"] = field
        return result

    @staticmethod
    def _parse_timestamp(value: Any, issues: list[dict[str, Any]]) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            issues.append(ChamberDataQualityGate._issue("invalid_timestamp", REJECT, f"Invalid observed_at={value!r}"))
            return None
        return parsed

    def _signature(self, sample: dict[str, Any]) -> str:
        payload = {field: sample.get(field) for field in self.required_fields if field != "observed_at"}
        return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))

    @staticmethod
    def _issue_identity(issue: dict[str, Any]) -> str:
        return f"{issue.get('code', 'unknown')}:{issue.get('field', '*')}"
