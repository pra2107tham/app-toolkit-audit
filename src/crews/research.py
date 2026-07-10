"""Pass-1 research crew: Docs Scout finds authoritative URLs, API Analyst reads
them and fills the structured AppRecord. Both run on the OpenAI cheap tier."""
from crewai import Agent, Crew, Process, Task

from ..models import AppRecord


def make_research_crew(llm, scout_tools, analyst_tools) -> Crew:
    scout = Agent(
        role="Developer Docs Scout",
        goal="Find the authoritative developer documentation, auth docs, and API-access pricing pages for a SaaS app",
        backstory=(
            "You research apps before an integrations team builds toolkits for them. "
            "You only trust official vendor domains and well-known doc portals."
        ),
        tools=scout_tools,
        llm=llm,
        max_iter=8,
        allow_delegation=False,
        verbose=False,
    )

    analyst = Agent(
        role="API Integration Analyst",
        goal="Determine auth methods, credential access, and API surface for an app, with a citable evidence URL per field",
        backstory=(
            "You assess whether an app can become an AI-agent toolkit today. You never assert a fact "
            "without a page you actually read supporting it, and you say 'unclear' when docs are missing."
        ),
        tools=analyst_tools,
        llm=llm,
        max_iter=12,
        allow_delegation=False,
        verbose=False,
    )

    scout_task = Task(
        description=(
            "Research the app {name} (category: {category}, homepage: {homepage}, hint: {hint}).\n"
            "Find URLs for: 1) the developer/API documentation root, 2) the API authentication docs page, "
            "3) the page showing how a developer obtains API credentials (signup, dashboard, pricing or plan page), "
            "4) any page showing that API access requires a partnership, paid plan, or contact-sales approval.\n"
            "Known deterministic context you do NOT need to research: {prefacts}\n"
            "Prefer official vendor domains. If searches return nothing relevant, say so explicitly and list the "
            "exact queries you tried."
        ),
        expected_output="A short list of URLs, each with a one-line note on what it shows, or an explicit statement that no public developer docs were found plus the queries tried.",
        agent=scout,
    )

    analyst_task = Task(
        description=(
            "Using the scout's URLs, fetch the important ones to confirm their content, then fill the structured "
            "record for {name} (slug: {slug}, category: {category}).\n"
            "Rules:\n"
            "- Every field's evidence_url must be a page you SUCCESSFULLY FETCHED (with either fetch tool) and that "
            "actually supports the value; never cite a URL from memory or from a search snippet alone. Prefer "
            "official docs.\n"
            "- auth_methods: the methods the public API supports (oauth2, api_key, basic, token, jwt, other, none_public).\n"
            "- credential_access: self_serve_free if a developer can get working API credentials on a free plan or "
            "trial without human approval; self_serve_paid_trial if a paid plan or card is required but no approval; "
            "admin_approval if a human/org admin must approve; partner_gated if API access requires a partnership or "
            "contact-sales; unclear only when docs do not say.\n"
            "- api_surface: rest/graphql/both/sdk_only/none_public plus breadth (broad = most product objects are "
            "covered by the API, narrow = only a few endpoints) and the docs root URL.\n"
            "- buildability: buildable_today when docs + self-serve credentials exist; buildable_with_key_signup when "
            "it only needs a routine key signup; blocked otherwise, with main_blocker set.\n"
            "- description: one line on what the app does.\n"
            "- If no public developer docs exist after real searching, set research_meta.insufficient_public_docs to "
            "true, use 'unclear'/'none_public' values, record the queries tried in research_meta.queries, and only "
            "then may evidence_url fields be empty.\n"
            "- Set confidence per field: high only when you read an official page stating it outright."
        ),
        expected_output="A complete AppRecord for the app with per-field evidence URLs and confidence.",
        agent=analyst,
        context=[scout_task],
        output_pydantic=AppRecord,
    )

    return Crew(agents=[scout, analyst], tasks=[scout_task, analyst_task], process=Process.sequential, verbose=False)
