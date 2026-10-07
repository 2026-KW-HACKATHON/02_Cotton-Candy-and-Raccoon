"""Load dictionary keys locally without exposing them or changing other settings."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

DEFAULT_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


class GlossaryConfigurationError(ValueError):
    """A dictionary key needed for the current lookup is missing."""

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider


def _local_values(dotenv_path: Path, names: set[str]) -> dict[str, str]:
    """Read only glossary keys; environment values take precedence."""
    try:
        lines = dotenv_path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError):
        raise GlossaryConfigurationError("용어 API 설정 파일을 읽을 수 없습니다.") from None
    values = {}
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if name not in names:
            continue
        if value.startswith(("'", '"')):
            end = value.find(value[0], 1)
            if end < 0 or (suffix := value[end + 1 :].strip()) and not suffix.startswith("#"):
                raise GlossaryConfigurationError(
                    "용어 API 설정의 따옴표 또는 주석이 잘못되었습니다."
                )
            value = value[1:end]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        values[name] = value
    return values


@dataclass(frozen=True, slots=True)
class GlossarySettings:
    """Load Standard Korean Dictionary and Ourmalsam credentials only as needed."""

    opendict_api_key: str = field(default="", repr=False)
    stdict_api_key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls, dotenv_path: Path | None = None, *, provider: str | None = None) -> Self:
        key_names = {"stdict": "STDICT_API_KEY", "opendict": "OPENDICT_API_KEY"}
        if provider is not None and provider not in key_names:
            raise GlossaryConfigurationError("지원하지 않는 사전입니다.")
        names = (key_names[provider],) if provider else tuple(key_names.values())
        environment = {name: os.environ.get(name, "").strip() for name in names}
        try:
            local = (
                _local_values(dotenv_path or DEFAULT_ENV_PATH, set(names))
                if not all(environment.values())
                else {}
            )
        except GlossaryConfigurationError as exc:
            raise GlossaryConfigurationError(str(exc), provider=provider) from None
        return cls(
            opendict_api_key=environment.get("OPENDICT_API_KEY", "")
            or local.get("OPENDICT_API_KEY", ""),
            stdict_api_key=environment.get("STDICT_API_KEY", "") or local.get("STDICT_API_KEY", ""),
        )

    def key_for(self, provider: str) -> str:
        if provider not in ("stdict", "opendict"):
            raise GlossaryConfigurationError("지원하지 않는 사전입니다.")
        key = self.stdict_api_key if provider == "stdict" else self.opendict_api_key
        name = "STDICT_API_KEY" if provider == "stdict" else "OPENDICT_API_KEY"
        if not key.strip():
            raise GlossaryConfigurationError(
                f"services/pipeline/.env에 {name}를 설정하세요.", provider=provider
            )
        return key.strip()
