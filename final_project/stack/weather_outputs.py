"""Identifiers the create scripts generate, saved as JSON (for Python) and .env (for bash)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


def _save(data: dict, stem: str, directory: str | Path) -> None:
    directory = Path(directory)
    (directory / f"{stem}.json").write_text(json.dumps(data, indent=2) + "\n")
    lines = [f"{key.upper()}={value}" for key, value in data.items()]
    (directory / f"{stem}.env").write_text("\n".join(lines) + "\n")


def _load(stem: str, directory: str | Path) -> dict:
    return json.loads((Path(directory) / f"{stem}.json").read_text())


def _remove(stem: str, directory: str | Path) -> None:
    for suffix in (".json", ".env"):
        (Path(directory) / f"{stem}{suffix}").unlink(missing_ok=True)


@dataclass
class DatabaseOutputs:
    """What create_database.py produces. Never holds the password."""
    db_host: str
    db_security_group_id: str

    STEM = "db_outputs"

    def save(self, directory: str | Path = ".") -> None:
        _save(asdict(self), self.STEM, directory)

    @classmethod
    def load(cls, directory: str | Path = ".") -> "DatabaseOutputs":
        return cls(**_load(cls.STEM, directory))

    @classmethod
    def remove(cls, directory: str | Path = ".") -> None:
        _remove(cls.STEM, directory)


@dataclass
class WeatherStackOutputs:
    """What create_weather_stack.py produces."""
    vpc_id: str
    subnet_a: str
    subnet_b: str
    alb_security_group_id: str
    alb_arn: str
    alb_dns_name: str
    weather_tg_arn: str
    advisor_tg_arn: str

    STEM = "stack_outputs"

    def tg_arn(self, tier_name: str) -> str:
        return {"weather": self.weather_tg_arn, "advisor": self.advisor_tg_arn}[tier_name]

    def save(self, directory: str | Path = ".") -> None:
        _save(asdict(self), self.STEM, directory)

    @classmethod
    def load(cls, directory: str | Path = ".") -> "WeatherStackOutputs":
        return cls(**_load(cls.STEM, directory))

    @classmethod
    def remove(cls, directory: str | Path = ".") -> None:
        _remove(cls.STEM, directory)
