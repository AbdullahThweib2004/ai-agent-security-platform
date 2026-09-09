"""ORM models.

Importing the package registers every model on ``Base.metadata``, so schema
creation cannot miss one because an import was forgotten.
"""

from app.models.a2a import A2ADecision
from app.models.base import Base
from app.models.delegation import Delegation
from app.models.event import AgentEvent, Alert
from app.models.identity import AgentIdentity
from app.models.incident import Incident, IncidentEvent

__all__ = [
    "A2ADecision",
    "AgentEvent",
    "AgentIdentity",
    "Alert",
    "Base",
    "Delegation",
    "Incident",
    "IncidentEvent",
]
