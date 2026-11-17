"""Settings for the weather advisor lab, read from .env. Nothing is hard-coded."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, Tier, load_dotenv

TIER_KEYS = ["ROLE_NAME", "PROFILE_NAME", "LT_NAME", "TG_NAME", "ASG_NAME", "LISTENER_PORT"]

REQUIRED_KEYS = [
    "AWS_REGION", "AMI_ID", "KEY_NAME", "INSTANCE_TYPE", "APP_SECURITY_GROUP_ID",
    "AZ_A", "AZ_B", "APP_PORT", "ALB_NAME", "ALB_SG_NAME",
    *[f"WEATHER_{k}" for k in TIER_KEYS], *[f"ADVISOR_{k}" for k in TIER_KEYS],
    "DB_INSTANCE_ID", "DB_INSTANCE_CLASS", "DB_ALLOCATED_STORAGE", "DB_PORT", "DB_NAME",
    "DB_USER", "DB_SG_NAME", "DB_SUBNET_GROUP_NAME", "DB_PASSWORD_PARAM", "CACHE_SECONDS",
    "GEMINI_PARAM_NAME", "GEMINI_MODEL", "KEY_CACHE_SECONDS", "CONTACT_EMAIL",
]


@dataclass(frozen=True)
class WeatherSettings:
    region: str
    ami_id: str
    key_name: str
    instance_type: str
    app_sg_id: str
    az_a: str
    az_b: str
    app_port: int
    alb_name: str
    alb_sg_name: str
    weather: Tier
    advisor: Tier
    db_instance_id: str
    db_instance_class: str
    db_allocated_storage: int
    db_port: int
    db_name: str
    db_user: str
    db_sg_name: str
    db_subnet_group_name: str
    db_password_param: str
    cache_seconds: int
    gemini_param_name: str
    gemini_model: str
    key_cache_seconds: int
    contact_email: str

    @property
    def tiers(self) -> tuple[Tier, Tier]:
        return (self.weather, self.advisor)

    def tier(self, name: str) -> Tier:
        return {"weather": self.weather, "advisor": self.advisor}[name]

    def parameter_arn(self, account_id: str, name: str) -> str:
        """ARN of an SSM parameter. Names start with '/', so the ARN has a single slash."""
        return f"arn:aws:ssm:{self.region}:{account_id}:parameter{name}"

    @classmethod
    def from_values(cls, v: dict[str, str], base_dir: str | Path = ".") -> "WeatherSettings":
        missing = [k for k in REQUIRED_KEYS if not v.get(k)]
        if missing:
            raise ConfigError("Missing keys in .env: " + ", ".join(missing))
        base = Path(base_dir)

        def as_int(key: str) -> int:
            try:
                return int(v[key])
            except ValueError:
                raise ConfigError(f"{key} must be a whole number, got {v[key]!r}") from None

        def tier(prefix: str) -> Tier:
            return Tier(
                name=prefix.lower(),
                role_name=v[f"{prefix}_ROLE_NAME"],
                profile_name=v[f"{prefix}_PROFILE_NAME"],
                lt_name=v[f"{prefix}_LT_NAME"],
                tg_name=v[f"{prefix}_TG_NAME"],
                asg_name=v[f"{prefix}_ASG_NAME"],
                listener_port=as_int(f"{prefix}_LISTENER_PORT"),
                app_dir=base / f"{prefix.lower()}-app",
            )

        return cls(
            region=v["AWS_REGION"], ami_id=v["AMI_ID"], key_name=v["KEY_NAME"],
            instance_type=v["INSTANCE_TYPE"], app_sg_id=v["APP_SECURITY_GROUP_ID"],
            az_a=v["AZ_A"], az_b=v["AZ_B"], app_port=as_int("APP_PORT"),
            alb_name=v["ALB_NAME"], alb_sg_name=v["ALB_SG_NAME"],
            weather=tier("WEATHER"), advisor=tier("ADVISOR"),
            db_instance_id=v["DB_INSTANCE_ID"], db_instance_class=v["DB_INSTANCE_CLASS"],
            db_allocated_storage=as_int("DB_ALLOCATED_STORAGE"), db_port=as_int("DB_PORT"),
            db_name=v["DB_NAME"], db_user=v["DB_USER"], db_sg_name=v["DB_SG_NAME"],
            db_subnet_group_name=v["DB_SUBNET_GROUP_NAME"],
            db_password_param=v["DB_PASSWORD_PARAM"], cache_seconds=as_int("CACHE_SECONDS"),
            gemini_param_name=v["GEMINI_PARAM_NAME"], gemini_model=v["GEMINI_MODEL"],
            key_cache_seconds=as_int("KEY_CACHE_SECONDS"), contact_email=v["CONTACT_EMAIL"],
        )

    @classmethod
    def from_file(cls, path: str | Path = ".env") -> "WeatherSettings":
        path = Path(path)
        return cls.from_values(load_dotenv(path), base_dir=path.resolve().parent)
