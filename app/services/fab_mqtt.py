"""MQTT transport and direct-dispatch bridge for FAB v2 events.

The router is transport agnostic: tests and local debugging can dispatch the
same validated envelope directly, while the production-like local path sends
it through Mosquitto with QoS 1.  Consumer receipts are persisted separately
so redelivery never duplicates writes or inference work.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = "fab.v2"
SUPPORTED_MESSAGE_TYPES = {"telemetry", "state", "event", "metrology", "inspection"}
REQUIRED_IDENTITY_FIELDS = {
    "schema_version",
    "message_id",
    "lot_id",
    "wafer_id",
    "process_run_id",
    "process_id",
    "process_step",
    "equipment_id",
    "unit_id",
    "unit_type",
    "recipe_id",
    "cycle_id",
    "cycle_index",
    "cycle_index_since_maintenance",
    "machine_state",
    "phase",
    "phase_progress",
    "observed_at",
}


class FabMessageError(ValueError):
    """Raised when an incoming FAB envelope violates the public contract."""


class FabConsumerError(RuntimeError):
    """Raised by direct transport when a routed consumer could not finish."""


def _utc_timestamp(value: object) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise FabMessageError("observed_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise FabMessageError("observed_at must include an explicit UTC offset")
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def validate_envelope(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return a normalized copy of a FAB message or raise ``FabMessageError``."""
    if not isinstance(raw, Mapping):
        raise FabMessageError("FAB message must be a JSON object")
    message = dict(raw)
    if message.get("schema_version") != SCHEMA_VERSION:
        raise FabMessageError(f"schema_version must be {SCHEMA_VERSION!r}")
    if not str(message.get("message_id") or "").strip():
        raise FabMessageError("message_id is required")
    message_type = str(message.get("message_type") or "").lower()
    if message_type not in SUPPORTED_MESSAGE_TYPES:
        raise FabMessageError(f"unsupported message_type: {message_type!r}")
    identity = message.get("identity")
    if not isinstance(identity, Mapping):
        raise FabMessageError("identity must be an object")
    missing = sorted(
        field
        for field in REQUIRED_IDENTITY_FIELDS
        if identity.get(field) is None
        or (isinstance(identity.get(field), str) and not str(identity.get(field)).strip())
    )
    if missing:
        raise FabMessageError(f"identity is missing: {', '.join(missing)}")
    normalized_identity = dict(identity)
    if normalized_identity["schema_version"] != message["schema_version"]:
        raise FabMessageError("identity.schema_version must match envelope schema_version")
    if normalized_identity["message_id"] != message["message_id"]:
        raise FabMessageError("identity.message_id must match envelope message_id")
    normalized_identity["observed_at"] = _utc_timestamp(identity["observed_at"])
    try:
        normalized_identity["phase_progress"] = float(identity["phase_progress"])
        normalized_identity["cycle_index_since_maintenance"] = int(
            identity["cycle_index_since_maintenance"]
        )
        normalized_identity["cycle_index"] = int(identity["cycle_index"])
    except (TypeError, ValueError) as exc:
        raise FabMessageError("phase/cycle fields must be numeric") from exc
    if not 0.0 <= normalized_identity["phase_progress"] <= 1.0:
        raise FabMessageError("phase_progress must be between 0 and 1")
    if normalized_identity["cycle_index"] < 1:
        raise FabMessageError("cycle_index must be at least 1")
    if normalized_identity["cycle_index_since_maintenance"] < 1:
        raise FabMessageError("cycle_index_since_maintenance must be at least 1")
    payload = message.get("payload", {})
    if not isinstance(payload, Mapping):
        raise FabMessageError("payload must be an object")
    message["message_type"] = message_type
    message["identity"] = normalized_identity
    message["payload"] = dict(payload)
    return message


def topic_for(message: Mapping[str, Any]) -> str:
    envelope = validate_envelope(message)
    identity = envelope["identity"]
    process = str(identity["process_step"]).lower()
    if envelope["message_type"] in {"metrology", "inspection"}:
        return f"fab/{envelope['message_type']}/{process}/{identity['wafer_id']}"
    return (
        f"fab/{process}/{identity['equipment_id']}/{identity['unit_id']}/"
        f"{envelope['message_type']}"
    )


@dataclass
class _Counters:
    received: int = 0
    published: int = 0
    duplicates: int = 0
    malformed: int = 0
    consumer_errors: int = 0
    reconnects: int = 0
    latest_observed_at: str | None = None
    total_detection_latency_ms: float = 0.0
    detection_count: int = 0


class FabTransportMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._counters = _Counters()

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            setattr(self._counters, name, int(getattr(self._counters, name)) + amount)

    def observe(self, envelope: Mapping[str, Any], latency_ms: float | None = None) -> None:
        with self._lock:
            self._counters.latest_observed_at = str(envelope["identity"]["observed_at"])
            if latency_ms is not None:
                self._counters.total_detection_latency_ms += float(latency_ms)
                self._counters.detection_count += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            values = vars(self._counters).copy()
        elapsed = max(1e-6, time.monotonic() - self._started)
        count = int(values.pop("detection_count"))
        total = float(values.pop("total_detection_latency_ms"))
        values["messages_per_second"] = round(values["received"] / elapsed, 4)
        values["detection_latency_ms_avg"] = round(total / count, 4) if count else None
        return values


transport_metrics = FabTransportMetrics()


def _reason_value(reason_code: Any) -> int:
    """Normalize Paho v2 ``ReasonCode`` and legacy integer callbacks."""
    value = getattr(reason_code, "value", reason_code)
    return int(value)


Consumer = Callable[[dict[str, Any]], Any]


class FabMessageRouter:
    """Validate once, then deliver idempotently to named consumers."""

    def __init__(self, consumers: Mapping[str, Consumer] | None = None) -> None:
        self.consumers: dict[str, Consumer] = dict(consumers or {})

    def register(self, name: str, consumer: Consumer) -> None:
        if not name or name in self.consumers:
            raise ValueError(f"consumer already registered or invalid: {name!r}")
        self.consumers[name] = consumer

    @staticmethod
    def _claim(message_id: str, consumer_name: str) -> bool:
        from app.services import fab_storage  # lazy: no DB startup side effects on import

        return fab_storage.claim_message(message_id, consumer_name)

    def route(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        try:
            envelope = validate_envelope(raw)
        except FabMessageError:
            transport_metrics.increment("malformed")
            raise
        transport_metrics.increment("received")
        transport_metrics.observe(envelope)
        delivered: list[str] = []
        duplicates: list[str] = []
        errors: dict[str, str] = {}
        for name, consumer in self.consumers.items():
            if not self._claim(str(envelope["message_id"]), name):
                transport_metrics.increment("duplicates")
                duplicates.append(name)
                continue
            started = time.perf_counter()
            try:
                consumer(envelope)
                from app.services import fab_storage

                fab_storage.mark_message_processed(str(envelope["message_id"]), name)
                delivered.append(name)
                if name == "detector":
                    transport_metrics.observe(envelope, (time.perf_counter() - started) * 1000.0)
            except Exception as exc:  # noqa: BLE001 - worker boundary must isolate consumers
                transport_metrics.increment("consumer_errors")
                errors[name] = f"{type(exc).__name__}: {exc}"
                from app.services import fab_storage

                fab_storage.release_message(str(envelope["message_id"]), name)
        return {
            "message_id": envelope["message_id"],
            "delivered": delivered,
            "duplicates": duplicates,
            "errors": errors,
        }


class DirectFabTransport:
    def __init__(self, router: FabMessageRouter) -> None:
        self.router = router

    def publish(self, message: Mapping[str, Any]) -> dict[str, Any]:
        transport_metrics.increment("published")
        result = self.router.route(message)
        if result["errors"]:
            raise FabConsumerError(f"FAB consumers failed: {result['errors']}")
        return result

    def close(self) -> None:
        return None


class MqttFabPublisher:
    def __init__(self) -> None:
        try:
            import paho.mqtt.client as mqtt  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - dependency error is actionable
            raise RuntimeError("paho-mqtt is required for --transport mqtt") from exc
        self._mqtt = mqtt
        self.host = os.environ.get("MQTT_HOST", "127.0.0.1")
        self.port = int(os.environ.get("MQTT_PORT", "1883"))
        self._connected = threading.Event()
        self._connected_once = False
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self._on_connect
        self.client.connect(self.host, self.port, keepalive=30)
        self.client.loop_start()
        timeout = float(os.environ.get("MQTT_CONNECT_TIMEOUT_SECONDS", "10"))
        if not self._connected.wait(timeout=max(0.1, timeout)):
            self.close()
            raise TimeoutError(f"MQTT publisher did not connect to {self.host}:{self.port}")

    def _on_connect(self, _client, _userdata, _flags, reason_code, _properties) -> None:
        if _reason_value(reason_code) == 0:
            if self._connected_once:
                transport_metrics.increment("reconnects")
            self._connected_once = True
            self._connected.set()
        else:
            transport_metrics.increment("reconnects")

    def publish(self, message: Mapping[str, Any]) -> dict[str, Any]:
        envelope = validate_envelope(message)
        payload = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        info = self.client.publish(topic_for(envelope), payload, qos=1)
        info.wait_for_publish(timeout=10)
        if info.rc != self._mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish failed with rc={info.rc}")
        transport_metrics.increment("published")
        return {"message_id": envelope["message_id"], "topic": topic_for(envelope), "published": True}

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


class MqttFabSubscriber:
    def __init__(self, router: FabMessageRouter) -> None:
        try:
            import paho.mqtt.client as mqtt  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("paho-mqtt is required for MQTT subscription") from exc
        self.router = router
        self.host = os.environ.get("MQTT_HOST", "127.0.0.1")
        self.port = int(os.environ.get("MQTT_PORT", "1883"))
        self._ready = threading.Event()
        self._connected_once = False
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=os.environ.get("MQTT_CONSUMER_CLIENT_ID", "waferguard-fab-v2-consumer"),
            clean_session=False,
        )
        self.client.manual_ack_set(True)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self._on_connect
        self.client.on_subscribe = self._on_subscribe
        self.client.on_message = self._on_message

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties) -> None:
        if _reason_value(reason_code) == 0:
            if self._connected_once:
                transport_metrics.increment("reconnects")
            self._connected_once = True
            self._ready.clear()
            client.subscribe("fab/#", qos=1)
        else:
            transport_metrics.increment("reconnects")

    def _on_subscribe(self, _client, _userdata, _mid, reason_codes, _properties) -> None:
        codes = reason_codes if isinstance(reason_codes, (list, tuple)) else [reason_codes]
        if codes and all(_reason_value(code) < 128 for code in codes):
            self._ready.set()

    def _on_message(self, client, _userdata, message) -> None:
        try:
            parsed = json.loads(message.payload.decode("utf-8"))
            result = self.router.route(parsed)
            if result["errors"]:
                # Leave the QoS-1 message unacknowledged.  The durable session
                # can redeliver after reconnect and receipts release only the
                # consumers that failed.
                return
            client.ack(message.mid, message.qos)
        except FabMessageError:
            # ``route`` already counted a schema rejection.
            client.ack(message.mid, message.qos)
            return
        except (UnicodeDecodeError, json.JSONDecodeError):
            transport_metrics.increment("malformed")
            client.ack(message.mid, message.qos)

    def start(self) -> None:
        self.client.connect(self.host, self.port, keepalive=30)
        self.client.loop_start()
        timeout = float(os.environ.get("MQTT_CONNECT_TIMEOUT_SECONDS", "10"))
        if not self._ready.wait(timeout=max(0.1, timeout)):
            self.close()
            raise TimeoutError(f"MQTT subscriber did not subscribe at {self.host}:{self.port}")

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


def make_transport(name: str, router: FabMessageRouter):
    normalized = str(name).strip().lower()
    if normalized == "direct":
        return DirectFabTransport(router)
    if normalized == "mqtt":
        return MqttFabPublisher()
    raise ValueError("transport must be 'mqtt' or 'direct'")
