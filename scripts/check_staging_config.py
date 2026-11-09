"""Fail-closed staging configuration validator; no secrets are printed."""
import os


def check_staging_config(env):
    required = ("OSIRIS_API_TOKEN", "OSIRIS_OBSERVATION_HMAC_KEY", "OSIRIS_TRUSTED_PEERS")
    missing = [key for key in required if not isinstance(env.get(key), str) or not env[key].strip()]
    if env.get("OSIRIS_ENV") != "staging":
        missing.append("OSIRIS_ENV_must_be_staging")
    if env.get("OSIRIS_LIVE_TRADING_ENABLED", "0").lower() not in ("0", "false"):
        missing.append("live_trading_must_be_disabled")
    return {"ready": not missing, "failed_checks": missing}


if __name__ == "__main__":
    result = check_staging_config(os.environ)
    print("staging config valid" if result["ready"] else "staging config blocked: " + ", ".join(result["failed_checks"]))
    raise SystemExit(0 if result["ready"] else 1)
