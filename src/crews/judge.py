"""Eval-layer LLM judge: a third model lineage scores field↔evidence
faithfulness on a stratified sample of FINAL records. Tool-free single-shot:
evidence pages are pre-fetched and injected, so cost is deterministic and the
judge cannot skip reading."""
from crewai import Agent, Crew, Process, Task

from ..models import JudgeReport


def make_judge_crew(llm) -> Crew:
    judge = Agent(
        role="Evidence Faithfulness Judge",
        goal="Score whether each recorded value is actually supported by its cited evidence page",
        backstory=(
            "You are an independent evaluator. You have no stake in the research being right. You judge only what "
            "the evidence text in front of you says."
        ),
        llm=llm,
        allow_delegation=False,
        verbose=False,
    )

    judge_task = Task(
        description=(
            "Evaluate the faithfulness of this final research record for {name} (slug: {slug}).\n"
            "Record:\n{record_json}\n\n"
            "The content of every cited evidence page has been pre-fetched:\n"
            "----- PRE-FETCHED EVIDENCE -----\n{evidence_dump}\n----- END EVIDENCE -----\n\n"
            "For each of the five researched fields (description, auth_methods, credential_access, api_surface, "
            "buildability): decide faithful=true only if the pre-fetched page text supports the recorded value. "
            "A field with insufficient_public_docs=true and an empty evidence_url is faithful only if its value is "
            "'unclear' or 'none_public' and the homepage content does not obviously contradict that. A truncated "
            "page that still supports the claim counts. Give one score per field with a one-line reason."
        ),
        expected_output="A JudgeReport with five field scores.",
        agent=judge,
        output_pydantic=JudgeReport,
    )

    return Crew(agents=[judge], tasks=[judge_task], process=Process.sequential, verbose=False)
