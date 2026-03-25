"""Company module — employees, teams, and organizational management."""

from shipwright.company.company import Company, Team
from shipwright.company.employee import (
    DelegationRequest,
    Employee,
    EmployeeStatus,
    LeadResponse,
    MemberResult,
    Task,
    parse_delegations,
)
from shipwright.company.roles import (
    BUILTIN_CREWS,
    BUILTIN_ROLES,
    ROLE_DISPLAY_NAMES,
    TEAM_TEMPLATES,
    get_crew_def,
    get_role_def,
    get_specialist_def,
    inspect_crew,
    inspect_role,
    list_crew_types,
    list_installed,
    list_roles,
    list_specialists,
    specialist_as_crew,
)

__all__ = [
    "BUILTIN_CREWS",
    "BUILTIN_ROLES",
    "Company",
    "DelegationRequest",
    "Employee",
    "EmployeeStatus",
    "LeadResponse",
    "MemberResult",
    "ROLE_DISPLAY_NAMES",
    "TEAM_TEMPLATES",
    "Task",
    "Team",
    "get_crew_def",
    "get_role_def",
    "get_specialist_def",
    "inspect_crew",
    "inspect_role",
    "list_crew_types",
    "list_installed",
    "list_roles",
    "list_specialists",
    "parse_delegations",
    "specialist_as_crew",
]
