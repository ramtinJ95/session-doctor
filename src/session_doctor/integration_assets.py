from __future__ import annotations

from importlib.resources import files
from pathlib import Path


class IntegrationAssetError(RuntimeError):
    pass


def session_doctor_skill_directory() -> Path:
    skill_directory = Path(
        str(files("session_doctor").joinpath("integrations", "session-doctor"))
    ).resolve()
    if not skill_directory.is_dir() or not (skill_directory / "SKILL.md").is_file():
        raise IntegrationAssetError(
            "Bundled session-doctor skill is unavailable. Reinstall session-doctor."
        )
    return skill_directory
