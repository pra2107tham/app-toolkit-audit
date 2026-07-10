"""Fixer: targeted re-research of only the fields the verifier flagged, on the
OpenAI smart tier. Outputs a full AppRecord; the orchestrator merges only the
flagged fields into the final record."""
from crewai import Agent, Crew, Process, Task

from ..models import AppRecord


def make_fix_crew(llm, tools) -> Crew:
    fixer = Agent(
        role="Senior Integration Researcher",
        goal="Resolve disputed research fields with authoritative primary sources",
        backstory=(
            "You are called in when a first-pass researcher and a fact-checker disagree. You settle disputes by "
            "reading official documentation, not by guessing."
        ),
        tools=tools,
        llm=llm,
        max_iter=10,
        allow_delegation=False,
        verbose=False,
    )

    fix_task = Task(
        description=(
            "The following research record for {name} (slug: {slug}, category: {category}, homepage: {homepage}) has "
            "disputed fields.\n"
            "Current record:\n{record_json}\n\n"
            "Disputed fields and the fact-checker's findings:\n{disputes}\n\n"
            "Re-research ONLY the disputed fields from primary sources: search for and fetch the official docs pages, "
            "then produce the corrected full AppRecord. Keep undisputed fields exactly as they are. Every corrected "
            "field must cite an evidence_url you actually fetched, with confidence set honestly."
        ),
        expected_output="The corrected complete AppRecord.",
        agent=fixer,
        output_pydantic=AppRecord,
    )

    return Crew(agents=[fixer], tasks=[fix_task], process=Process.sequential, verbose=False)
