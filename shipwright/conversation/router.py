"""Router — handles user messages across multiple active crews.

The router is the central message handler. It:
- Manages multiple active crews
- Parses commands (hire, fire, status, etc.)
- Routes conversational messages to the active crew
- Provides the glue between interfaces and crews
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from shipwright.config import Config
from shipwright.conversation.session import Session
from shipwright.crew.crew import Crew, CrewStatus
from shipwright.crew.registry import (
    get_crew_def,
    get_specialist_def,
    inspect_crew,
    list_crew_types,
    list_installed,
    list_specialists,
)
from shipwright.utils.logging import get_logger
from shipwright.workspace.project import ProjectInfo, discover_project

logger = get_logger("conversation.router")


@dataclass
class Router:
    """Routes user messages to the right crew.

    Manages the lifecycle of crews: hiring, chatting, firing, and listing.
    Each session (CLI, Telegram channel, Discord channel) gets its own router.
    """

    config: Config
    session: Session
    crews: dict[str, Crew] = field(default_factory=dict)
    _project_info: ProjectInfo | None = field(default=None, repr=False)

    @property
    def project_info(self) -> ProjectInfo:
        if self._project_info is None:
            self._project_info = discover_project(self.config.repo_root)
        return self._project_info

    @property
    def active_crew(self) -> Crew | None:
        """Get the currently active crew."""
        if self.session.active_crew_id:
            return self.crews.get(self.session.active_crew_id)
        return None

    async def handle_message(
        self,
        text: str,
        on_text: Callable[[str], None] | None = None,
        on_delegation_start: Callable[[str, str, int, int], None] | None = None,
        on_delegation_end: Callable[[str, float, bool], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> str:
        """Process a user message and return the response.

        Handles commands (hire, fire, status, etc.) and routes
        conversational messages to the active crew.
        """
        text = text.strip()
        if not text:
            return ""

        self.session.add_user_message(text)

        # Check for commands
        command, response = self._try_command(text)
        if command:
            self.session.add_system_message(response)
            return response

        # Route to active crew
        crew = self.active_crew
        if not crew:
            # Try to auto-detect intent
            response = self._suggest_hire(text)
            self.session.add_system_message(response)
            return response

        # Chat with the active crew
        response = await crew.chat(
            user_message=text,
            on_text=on_text,
            on_delegation_start=on_delegation_start,
            on_delegation_end=on_delegation_end,
            on_progress=on_progress,
        )
        self.session.add_lead_message(response, crew_id=crew.id)
        return response

    def _try_command(self, text: str) -> tuple[bool, str]:
        """Try to parse text as a command. Returns (is_command, response)."""
        lower = text.lower().strip()

        # hire <crew_type> <objective>
        hire_match = re.match(
            r"^(?:hire|start|create)\s+(?:a\s+)?(\w+)(?:\s+crew)?\s+(?:for\s+|to\s+)?(.+)$",
            lower, re.IGNORECASE,
        )
        if hire_match:
            crew_type = hire_match.group(1)
            objective = text[hire_match.start(2):]  # preserve original casing
            return True, self._hire_crew(crew_type, objective)

        # fire <crew_id>
        fire_match = re.match(r"^(?:fire|dismiss|stop|remove)\s+(.+)$", lower)
        if fire_match:
            crew_id = fire_match.group(1).strip()
            return True, self._fire_crew(crew_id)

        # status
        if lower in ("status", "crews", "list", "board", "what's happening", "whats happening"):
            return True, self._status()

        # talk to <crew_id>
        talk_match = re.match(r"^(?:talk\s+to|switch\s+to|use)\s+(.+)$", lower)
        if talk_match:
            crew_id = talk_match.group(1).strip()
            return True, self._switch_crew(crew_id)

        # help
        if lower in ("help", "?", "commands"):
            return True, self._help()

        # log <crew_id>
        log_match = re.match(r"^(?:log|history)\s+(.+)$", lower)
        if log_match:
            crew_id = log_match.group(1).strip()
            return True, self._crew_log(crew_id)

        # ship / pr
        if lower in ("ship", "pr", "open pr", "create pr"):
            return True, self._ship()

        # shop / browse — list all available crews
        if lower in ("shop", "browse", "marketplace", "available"):
            return True, self._shop()

        # installed — list custom/installed crews
        if lower in ("installed", "plugins", "custom"):
            return True, self._installed()

        # inspect <crew>
        inspect_match = re.match(r"^inspect\s+(.+)$", lower)
        if inspect_match:
            name = inspect_match.group(1).strip()
            return True, self._inspect(name)

        # recruit <specialist> into <crew>
        recruit_match = re.match(
            r"^recruit\s+([\w-]+)\s+(?:into|to)\s+(.+)$", lower,
        )
        if recruit_match:
            specialist_name = recruit_match.group(1).strip()
            crew_id = recruit_match.group(2).strip()
            return True, self._recruit(specialist_name, crew_id)

        return False, ""

    def _hire_crew(self, crew_type: str, objective: str) -> str:
        """Hire a new crew."""
        try:
            crew_def = get_crew_def(crew_type, self.config)
        except ValueError as e:
            return str(e)

        project_context = self.project_info.to_prompt_context()

        crew = Crew.create(
            crew_type=crew_type,
            crew_def=crew_def,
            config=self.config,
            objective=objective,
            project_context=project_context,
        )

        self.crews[crew.id] = crew
        self.session.active_crew_id = crew.id

        members = ", ".join(
            f"{m.role}" for m in crew_def.members.values()
        )

        return (
            f"Hired **{crew_type}** crew: **{crew.id}**\n"
            f"Objective: {objective}\n"
            f"Team: {members}\n\n"
            f"You're now talking to the {crew_type} crew lead. What would you like to start with?"
        )

    def _fire_crew(self, crew_id: str) -> str:
        """Fire/dismiss a crew."""
        # Try exact match first, then partial
        crew = self.crews.get(crew_id)
        if not crew:
            for cid, c in self.crews.items():
                if crew_id in cid:
                    crew = c
                    crew_id = cid
                    break

        if not crew:
            return f"No crew found matching '{crew_id}'."

        crew.cleanup()
        del self.crews[crew_id]

        if self.session.active_crew_id == crew_id:
            self.session.active_crew_id = next(iter(self.crews), None)

        return f"Fired crew **{crew_id}**."

    def _status(self) -> str:
        """Show status of all crews."""
        if not self.crews:
            types = ", ".join(list_crew_types(self.config))
            return (
                "No active crews.\n\n"
                f"Available crew types: {types}\n"
                "Hire one with: `hire <type> <objective>`"
            )

        lines = ["**Active Crews**\n"]
        for crew in self.crews.values():
            active = " (active)" if crew.id == self.session.active_crew_id else ""
            lines.append(f"{crew.summary}{active}\n")
        return "\n".join(lines)

    def _switch_crew(self, crew_id: str) -> str:
        """Switch the active crew."""
        # Try exact match, then partial
        found = self.crews.get(crew_id)
        if not found:
            for cid, c in self.crews.items():
                if crew_id in cid:
                    found = c
                    crew_id = cid
                    break

        if not found:
            return f"No crew found matching '{crew_id}'."

        self.session.active_crew_id = crew_id
        return f"Now talking to **{crew_id}**."

    def _help(self) -> str:
        types = ", ".join(list_crew_types(self.config))
        return (
            "**Shipwright Commands**\n\n"
            f"  `hire <type> <objective>` — Hire a crew ({types})\n"
            "  `fire <crew-id>` — Dismiss a crew\n"
            "  `status` — Show all active crews\n"
            "  `talk to <crew-id>` — Switch active crew\n"
            "  `log <crew-id>` — View conversation history\n"
            "  `ship` — Open a PR for the active crew's work\n"
            "  `shop` — Browse all available crews & specialists\n"
            "  `installed` — List custom/installed crews\n"
            "  `inspect <name>` — Show crew/specialist details\n"
            "  `recruit <specialist> into <crew-id>` — Add specialist to a crew\n"
            "  `help` — Show this help\n\n"
            "Or just type naturally — messages go to the active crew lead."
        )

    def _crew_log(self, crew_id: str) -> str:
        """Show conversation log for a crew."""
        crew = self.crews.get(crew_id)
        if not crew:
            for cid, c in self.crews.items():
                if crew_id in cid:
                    crew = c
                    break

        if not crew:
            return f"No crew found matching '{crew_id}'."

        history = crew.lead.conversation_history
        if not history:
            return f"No conversation history for {crew.id}."

        lines = [f"**Conversation with {crew.id}**\n"]
        for msg in history[-20:]:
            role = "You" if msg["role"] == "user" else "Lead"
            text = msg["text"][:300]
            lines.append(f"**{role}:** {text}")
        return "\n\n".join(lines)

    async def _ship(self) -> str:
        """Open a PR for the active crew."""
        crew = self.active_crew
        if not crew:
            return "No active crew. Switch to a crew first."

        if not crew.worktree_path:
            return "This crew hasn't done any code changes yet."

        pr_url = await crew.ship()
        if pr_url:
            return f"PR opened: {pr_url}"
        return "Failed to open PR. Check the logs."

    def _shop(self) -> str:
        """List all available crews and specialists."""
        lines = ["**Available Crews & Specialists**\n"]

        lines.append("**Built-in Crews:**")
        from shipwright.crew.registry import BUILTIN_CREWS
        for name in sorted(BUILTIN_CREWS.keys()):
            cdef = BUILTIN_CREWS[name]
            members = ", ".join(m.role for m in cdef.members.values())
            lines.append(f"  `{name}` — {members}")

        # Custom crews
        custom = [
            (name, cdef) for name, cdef in self.config.custom_crews.items()
            if name not in BUILTIN_CREWS
        ]
        if custom:
            lines.append("\n**Custom Crews:**")
            for name, cdef in sorted(custom):
                desc = cdef.description or f"{len(cdef.members)} members"
                lines.append(f"  `{name}` [{cdef.source}] — {desc}")

        # Specialists
        specialists = list_specialists(self.config)
        if specialists:
            lines.append("\n**Specialists:**")
            for name in specialists:
                sdef = self.config.custom_specialists[name]
                desc = sdef.description or sdef.member_def.role
                lines.append(f"  `{name}` [{sdef.source}] — {desc}")

        lines.append(
            "\nUse `inspect <name>` for details, "
            "`hire <name> <objective>` to hire, "
            "or `recruit <specialist> into <crew-id>` to add to a running crew."
        )
        return "\n".join(lines)

    def _installed(self) -> str:
        """List custom/installed crews and specialists."""
        items = list_installed(self.config)
        if not items:
            return (
                "No custom crews or specialists installed.\n\n"
                "Add them to `./shipwright/crews/` or `~/.shipwright/crews/`."
            )

        lines = ["**Installed Crews & Specialists**\n"]
        for item in items:
            kind_tag = "crew" if item["kind"] == "crew" else "specialist"
            desc = item["description"] or "(no description)"
            lines.append(
                f"  `{item['name']}` ({kind_tag}) [{item['source']}] — {desc}"
            )
        return "\n".join(lines)

    def _inspect(self, name: str) -> str:
        """Show detailed info about a crew or specialist."""
        return inspect_crew(name, self.config)

    def _recruit(self, specialist_name: str, crew_id: str) -> str:
        """Recruit a specialist into an active crew."""
        specialist = get_specialist_def(specialist_name, self.config)
        if not specialist:
            available = list_specialists(self.config)
            if available:
                return (
                    f"Unknown specialist: '{specialist_name}'.\n"
                    f"Available: {', '.join(available)}"
                )
            return (
                f"Unknown specialist: '{specialist_name}'.\n"
                "No specialists installed. Add them to "
                "`./shipwright/crews/` or `~/.shipwright/crews/`."
            )

        # Find the crew (exact or partial match)
        crew = self.crews.get(crew_id)
        if not crew:
            for cid, c in self.crews.items():
                if crew_id in cid:
                    crew = c
                    crew_id = cid
                    break

        if not crew:
            return f"No active crew found matching '{crew_id}'."

        member_name = crew.recruit_specialist(specialist)
        return (
            f"Recruited **{specialist.member_def.role}** (`{member_name}`) "
            f"into crew **{crew_id}**.\n"
            f"The crew lead can now delegate work to `{member_name}`."
        )

    def _suggest_hire(self, text: str) -> str:
        """When no active crew, suggest hiring one."""
        types = ", ".join(list_crew_types(self.config))
        return (
            "No active crew. Hire one first!\n\n"
            f"Available types: {types}\n\n"
            "Examples:\n"
            "  `hire backend Add Stripe payments`\n"
            "  `hire frontend Redesign the dashboard`\n"
            "  `hire fullstack Build user authentication`\n\n"
            "Or type `help` for all commands."
        )

    def to_dict(self) -> dict:
        """Serialize router state."""
        return {
            "session": self.session.to_dict(),
            "crews": {cid: c.to_dict() for cid, c in self.crews.items()},
        }

    @classmethod
    def from_dict(cls, data: dict, config: Config) -> "Router":
        """Restore router from persisted data."""
        session = Session.from_dict(data.get("session", {"id": "default"}))
        router = cls(config=config, session=session)

        for cid, crew_data in data.get("crews", {}).items():
            try:
                crew_def = get_crew_def(crew_data["crew_type"], config)
                crew = Crew.from_dict(crew_data, crew_def, config)
                router.crews[cid] = crew
            except (ValueError, KeyError) as e:
                logger.warning("Failed to restore crew %s: %s", cid, e)

        return router
