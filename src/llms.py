"""LLM registry: OpenAI for research/fix, OpenRouter (different families) for
verification and judging so verifier errors decorrelate from researcher errors."""
import os

import litellm
from crewai import LLM
from dotenv import load_dotenv

load_dotenv()

# gpt-5-family (reasoning) models reject params like `stop` that CrewAI sends;
# let LiteLLM drop whatever a given model does not support.
litellm.drop_params = True


# gpt-5 family: reasoning models take no custom temperature and reject `stop`
# (litellm 1.72 predates them, so it must be told to drop it explicitly)

def openai_cheap() -> LLM:
    return LLM(
        model=f"openai/{os.getenv('OPENAI_CHEAP_MODEL', 'gpt-5-mini')}",
        api_key=os.environ["OPENAI_API_KEY"],
        additional_drop_params=["stop"],
    )


def openai_smart() -> LLM:
    """Fixer tier. Defaults to gpt-5-mini to fit a small credit budget; override
    with OPENAI_SMART_MODEL for a stronger model."""
    return LLM(
        model=f"openai/{os.getenv('OPENAI_SMART_MODEL', 'gpt-5-mini')}",
        api_key=os.environ["OPENAI_API_KEY"],
        additional_drop_params=["stop"],
    )


# Verifier and judge run tool-free single-shot calls over pre-fetched evidence,
# so their cost is deterministic. They use different OpenAI model lineages
# (4o vs 4.1 vs 5) than the researcher to keep their errors partially
# decorrelated after the cross-provider OpenRouter setup ran out of credits.

def verifier() -> LLM:
    return LLM(
        model=f"openai/{os.getenv('VERIFY_MODEL', 'gpt-4o-mini')}",
        api_key=os.environ["OPENAI_API_KEY"],
        temperature=0.1,
        max_tokens=4000,
    )


def judge() -> LLM:
    return LLM(
        model=f"openai/{os.getenv('JUDGE_MODEL', 'gpt-4.1-mini')}",
        api_key=os.environ["OPENAI_API_KEY"],
        temperature=0.1,
        max_tokens=3000,
    )
