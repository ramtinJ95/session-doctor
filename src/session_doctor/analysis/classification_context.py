from __future__ import annotations

from dataclasses import dataclass

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.schemas import MessageFeature, SessionFeature

from .classification_evidence import evidence_event_ids


@dataclass(frozen=True)
class ClassificationContext:
    bundle: ParsedSessionBundle
    analysis_run_id: str
    message_features: list[MessageFeature]
    session_features: dict[str, SessionFeature]

    @property
    def session_id(self) -> str:
        assert self.bundle.session is not None
        return self.bundle.session.session_id

    def int_feature(self, name: str) -> int:
        return int_feature(self.session_features, name)

    def float_feature(self, name: str) -> float:
        return float_feature(self.session_features, name)

    def bool_feature(self, name: str) -> bool:
        return bool_feature(self.session_features, name)

    def evidence_event_ids(self, feature_names: list[str]) -> list[str]:
        return evidence_event_ids(self.message_features, self.session_features, feature_names)

    def message_feature_values(self, feature_name: str) -> set[str]:
        return {
            feature.feature_value
            for feature in self.message_features
            if feature.feature_name == feature_name
        }


def int_feature(features: dict[str, SessionFeature], name: str) -> int:
    feature = features.get(name)
    if feature is None:
        return 0
    try:
        return int(float(feature.feature_value))
    except ValueError:
        return 0


def float_feature(features: dict[str, SessionFeature], name: str) -> float:
    feature = features.get(name)
    if feature is None:
        return 0.0
    try:
        return float(feature.feature_value)
    except ValueError:
        return 0.0


def bool_feature(features: dict[str, SessionFeature], name: str) -> bool:
    feature = features.get(name)
    return feature is not None and feature.feature_value == "true"
