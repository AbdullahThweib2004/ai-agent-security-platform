"""Alert vocabulary.

Rule names and severities are closed sets. Making them enums gives one source of
truth shared by the detector and the API, and turns a mistyped filter into a 422
instead of a silent empty list — which is indistinguishable from "no results"
and is exactly how a missed alert goes unnoticed.
"""

from __future__ import annotations

from enum import Enum


class AlertRule(str, Enum):
    UNSEEN_COUNTERPARTY = "unseen_counterparty"
    VALUE_EXCURSION = "value_excursion"
    NEW_PERMISSION = "new_permission"


class AlertSeverity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
