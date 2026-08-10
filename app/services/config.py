import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


def _load_secrets() -> None:
    """Optionally hydrate os.environ from AWS Secrets Manager.

    Runs only when USE_SECRETS_MANAGER=1, before other modules read env vars.
    Uses setdefault so an explicit env var / .env value always wins. boto3 is
    imported lazily so local dev (flag off) needs neither boto3 nor creds.
    """
    if os.environ.get("USE_SECRETS_MANAGER", "0").lower() not in ("1", "true", "yes"):
        return
    try:
        import json  # noqa: PLC0415

        from app.services import aws  # noqa: PLC0415

        secret_id = os.environ.get("SECRET_ID", "waferguard/app")
        raw = aws.secretsmanager().get_secret_value(SecretId=secret_id)["SecretString"]
        for key, value in json.loads(raw).items():
            if value is not None:
                os.environ.setdefault(key, str(value))
    except Exception:  # noqa: BLE001
        # Fall back to .env / existing environment; never block app startup.
        pass


_load_secrets()

OUTPUT_DIR = ROOT_DIR / "outputs"
IMAGE_DIR = OUTPUT_DIR / "images"
DB_PATH = OUTPUT_DIR / "waferguard.db"
RUNTIME_DIR = ROOT_DIR / "runtime"
CHAMBER_MODEL_DIR = RUNTIME_DIR / "models" / "chamber"

# --- Dual-mode backend toggles (default: all local, so dev is unchanged) ---
IMAGE_BACKEND = os.environ.get("IMAGE_BACKEND", "local").lower()      # local | s3
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "sqlite").lower()  # sqlite | postgres

MODEL_VERSION = "wafer-defectnet-v2.3.1"
DRIFT_THRESHOLD = 0.30
LOW_CONFIDENCE_THRESHOLD = 0.70
# Simulated retraining takes this long to finish: a triggered job is recorded as
# "running" immediately and only flips to "completed" (registering the Staging
# candidate) after the delay, so the lifecycle is observable instead of instant.
RETRAIN_DURATION_SECONDS = 20


def ensure_runtime_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    CHAMBER_MODEL_DIR.mkdir(parents=True, exist_ok=True)
