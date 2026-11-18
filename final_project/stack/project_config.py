"""Settings for the final project, read from .env. Names, ports, limits and models live
there, not in the code (Section 11.1)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, Tier, load_dotenv

TIER_KEYS = ["ROLE_NAME", "PROFILE_NAME", "LT_NAME", "TG_NAME", "ASG_NAME", "LISTENER_PORT"]

INT_KEYS = [
    "APP_PORT", "FACTS_FRESH_SECONDS", "FACTS_KEEP_SECONDS", "REPORT_SECONDS",
    "GEMINI_DAILY_CAP", "REPORT_LIMIT", "REPORT_WINDOW_SECONDS", "EMAIL_LIMIT", "EMAIL_IP_LIMIT",
    "EMAIL_WINDOW_SECONDS", "KEY_CACHE_SECONDS", "RECOVERY_SLO_SECONDS",
]
FLOAT_KEYS = ["OUTSIDE_TIMEOUT_SECONDS", "WEATHER_TIMEOUT_SECONDS", "GEMINI_TIMEOUT_SECONDS"]
STR_KEYS = [
    "AWS_REGION", "AMI_ID", "KEY_NAME", "INSTANCE_TYPE", "APP_SECURITY_GROUP_ID",
    "AZ_A", "AZ_B", "ALB_NAME", "ALB_SG_NAME", "FACTS_TABLE", "ADVISOR_TABLE",
    "SNS_TOPIC_NAME", "GEMINI_PARAM_NAME", "SALT_PARAM_NAME", "GEMINI_MODEL", "CONTACT_EMAIL",
]
REQUIRED_KEYS = STR_KEYS + INT_KEYS + FLOAT_KEYS + [
    f"{p}_{k}" for p in ("WEATHER", "ADVISOR") for k in TIER_KEYS]

# The ALB idle timeout is 60 s. Every outside call must end well before it (Section 9).
ALB_IDLE_TIMEOUT = 60


@dataclass(frozen=True)
class ProjectSettings:
    values: dict
    weather: Tier
    advisor: Tier

    # ---- convenience accessors used by the shared network/balancer/scaling code
    def __getattr__(self, name):
        key = name.upper()
        v = self.__dict__["values"]
        if key in v:
            return v[key]
        alias = {"region": "AWS_REGION", "app_sg_id": "APP_SECURITY_GROUP_ID"}.get(name)
        if alias:
            return v[alias]
        raise AttributeError(name)

    @property
    def tiers(self) -> tuple[Tier, Tier]:
        return (self.weather, self.advisor)

    def tier(self, name: str) -> Tier:
        return {"weather": self.weather, "advisor": self.advisor}[name]

    # ---- ARNs (exact resources for the IAM policies: no wildcards)
    def table_arn(self, account: str, table: str) -> str:
        return f"arn:aws:dynamodb:{self.region}:{account}:table/{table}"

    def parameter_arn(self, account: str, name: str) -> str:
        return f"arn:aws:ssm:{self.region}:{account}:parameter{name}"

    def topic_arn(self, account: str) -> str:
        return f"arn:aws:sns:{self.region}:{account}:{self.sns_topic_name}"

    @classmethod
    def from_values(cls, v: dict[str, str], base_dir: str | Path = ".") -> "ProjectSettings":
        missing = [k for k in REQUIRED_KEYS if not v.get(k)]
        if missing:
            raise ConfigError("Missing keys in .env: " + ", ".join(missing))
        typed: dict = {k: v[k] for k in STR_KEYS}
        for key in INT_KEYS + [f"{p}_LISTENER_PORT" for p in ("WEATHER", "ADVISOR")]:
            try:
                typed[key] = int(v[key])
            except ValueError:
                raise ConfigError(f"{key} must be a whole number, got {v[key]!r}") from None
        for key in FLOAT_KEYS:
            try:
                typed[key] = float(v[key])
            except ValueError:
                raise ConfigError(f"{key} must be a number, got {v[key]!r}") from None
        if typed["WEATHER_TIMEOUT_SECONDS"] + typed["GEMINI_TIMEOUT_SECONDS"] >= ALB_IDLE_TIMEOUT:
            raise ConfigError("WEATHER_TIMEOUT_SECONDS + GEMINI_TIMEOUT_SECONDS must stay under "
                              f"the ALB's {ALB_IDLE_TIMEOUT} s idle timeout.")
        if 3 * typed["OUTSIDE_TIMEOUT_SECONDS"] >= typed["WEATHER_TIMEOUT_SECONDS"]:
            raise ConfigError("The data tier makes up to 3 sequential outside calls: "
                              "3 x OUTSIDE_TIMEOUT_SECONDS must be under WEATHER_TIMEOUT_SECONDS.")
        base = Path(base_dir)

        def tier(prefix: str) -> Tier:
            return Tier(name=prefix.lower(), role_name=v[f"{prefix}_ROLE_NAME"],
                        profile_name=v[f"{prefix}_PROFILE_NAME"], lt_name=v[f"{prefix}_LT_NAME"],
                        tg_name=v[f"{prefix}_TG_NAME"], asg_name=v[f"{prefix}_ASG_NAME"],
                        listener_port=typed[f"{prefix}_LISTENER_PORT"],
                        app_dir=base / f"{prefix.lower()}_tier")

        return cls(values=typed, weather=tier("WEATHER"), advisor=tier("ADVISOR"))

    @classmethod
    def from_file(cls, path: str | Path = ".env") -> "ProjectSettings":
        path = Path(path)
        return cls.from_values(load_dotenv(path), base_dir=path.resolve().parent)
