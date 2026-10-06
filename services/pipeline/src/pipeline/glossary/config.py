"""Load dictionary keys locally without exposing them or changing other settings."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

DEFAULT_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


class GlossaryConfigurationError(ValueError):
    """A dictionary key needed for the current lookup is missing."""


def _local_values(dotenv_path: Path) -> dict[str, str]:
    """Read only glossary keys; environment values take precedence."""
    names = {"ONTERM_API_KEY", "OPENDICT_API_KEY"}
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
    """Use OnTerm then Ourmalsam, requiring each issued key only when it is used."""

    opendict_api_key: str = field(default="", repr=False)
    onterm_api_key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls, dotenv_path: Path | None = None) -> Self:
        names = ("OPENDICT_API_KEY", "ONTERM_API_KEY")
        env_values = {name: os.environ.get(name, "").strip() for name in names}
        local = (
            _local_values(dotenv_path or DEFAULT_ENV_PATH) if not all(env_values.values()) else {}
        )
        return cls(
            opendict_api_key=env_values[names[0]] or local.get(names[0], ""),
            onterm_api_key=env_values[names[1]] or local.get(names[1], ""),
        )

    def key_for(self, provider: str) -> str:
        if provider not in ("onterm", "opendict"):
            raise GlossaryConfigurationError("지원하지 않는 사전입니다.")
        keys = {
            "onterm": (self.onterm_api_key, "ONTERM_API_KEY"),
            "opendict": (self.opendict_api_key, "OPENDICT_API_KEY"),
        }
        key, name = keys[provider]
        if not key.strip():
            raise GlossaryConfigurationError(f"services/pipeline/.env에 {name}를 설정하세요.")
        return key.strip()
