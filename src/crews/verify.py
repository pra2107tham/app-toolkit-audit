"""Skeptic Verifier: a tool-free, single-shot fact-checker on a different model
lineage than the researcher. Evidence pages are pre-fetched deterministically
and injected into the prompt, so cost is fixed and the agent cannot skip
fetching. Corrections that need fresh research are the Fixer's job."""
from crewai import Agent, Crew, Process, Task

from ..models import VerificationReport


def make_verify_crew(llm) -> Crew:
    skeptic = Agent(
        role="Skeptical Integration Fact-Checker",
        goal="Catch every wrong or unsupported claim in an app research record",
        backstory=(
            "You are the adversarial reviewer. You assume the researcher was sloppy. A claim only counts as "
            "confirmed when the evidence text in front of you actually states it."
        ),
        llm=llm,
        allow_delegation=False,
        verbose=False,
    )

    verify_task = Task(
        description=(
            "Fact-check this research record for {name} (slug: {slug}, homepage: {homepage}).\n"
            "Record under review:\n{record_json}\n\n"
            "The content of every cited evidence page has ALREADY been fetched for you:\n"
            "----- PRE-FETCHED EVIDENCE -----\n{evidence_dump}\n----- END EVIDENCE -----\n\n"
            "For EACH of the five researched fields — description, auth_methods, credential_access, api_surface, "
            "buildability — grade the recorded value against the pre-fetched page content above:\n"
            "- confirmed: the page text genuinely supports the recorded value\n"
            "- contradicted: the page text shows a DIFFERENT value; put the correct value (plain text) in "
            "corrected_value\n"
            "- unsupported: the page loads (HTTP 200) but its text does not establish the claim\n"
            "- dead_url: the pre-fetched content shows HTTP 404/410/403 or FETCH_ERROR for that field's URL\n"
            "Grading rules: a truncated page that still supports the claim counts as confirmed — judge what the "
            "text says, not whether it is complete. Marketing pages can confirm the description but not API facts. "
            "If the record claims insufficient_public_docs=true, grade fields as confirmed only if 'unclear' or "
            "'none_public' is plausible given the homepage content. Grade buildability for consistency with the "
            "other four fields and their evidence.\n"
            "Return one verdict per field, all five fields, with short reasoning."
        ),
        expected_output="A VerificationReport with exactly five field verdicts.",
        agent=skeptic,
        output_pydantic=VerificationReport,
    )

    return Crew(agents=[skeptic], tasks=[verify_task], process=Process.sequential, verbose=False)
