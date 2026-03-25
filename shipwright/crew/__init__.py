"""Crew module — teams of specialized AI developers."""

from shipwright.crew.crew import Crew
from shipwright.crew.lead import CrewLead
from shipwright.crew.member import CrewMember
from shipwright.crew.registry import (
    get_crew_def,
    get_specialist_def,
    inspect_crew,
    list_crew_types,
    list_installed,
    list_specialists,
    specialist_as_crew,
)

__all__ = [
    "Crew",
    "CrewLead",
    "CrewMember",
    "get_crew_def",
    "get_specialist_def",
    "inspect_crew",
    "list_crew_types",
    "list_installed",
    "list_specialists",
    "specialist_as_crew",
]
