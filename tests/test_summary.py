import pytest

from sheetagent.agent import Agent
from sheetagent.summary import fact_sheet, template_summary, ungrounded_numbers, write_summary

from .conftest import ScriptedLLM


@pytest.fixture(scope="module")
def facts(demo):
    root, _ = demo
    agent = Agent()
    history = {"2026-08": {"revenue": 238311.96, "units": 8566.0, "orders": 386, "regions": {"North": 77291.6}}}
    return agent.report(agent.clean([root / "inbox" / "2026-09", root / "reference"], "2026-09"), history).facts


def test_template_summary_states_the_facts(facts):
    text = template_summary(facts)
    assert "September 2026 closed with revenue of 253,055 USD" in text
    assert "up 6.2% on August 2026" in text and "North was the largest region" in text
    assert ungrounded_numbers(text, "\n".join(fact_sheet(facts))) == []


def test_number_check_catches_invented_and_rounded_figures(facts):
    sheet = "\n".join(fact_sheet(facts))
    assert ungrounded_numbers("Revenue reached 253,055 USD, up 6.2%.", sheet) == []
    assert ungrounded_numbers("Revenue reached 253,100 USD.", sheet) == ["253,100"]
    assert ungrounded_numbers("Revenue was about 253K, up 7%.", sheet) == ["253", "7%"]
    assert ungrounded_numbers("Revenue fell 6.2%.", sheet) == []  # same figure; direction is wording, see limits
    assert ungrounded_numbers("The 3 regions sold well.", sheet) == []  # small counts are allowed


def test_model_summary_is_used_when_every_number_is_on_the_fact_sheet(facts):
    good = ("September 2026 brought revenue of 253,055 USD, which is +6.2% against August 2026. North led with "
            "91,457 USD while staying at 93.8% of its target. Watering was the strongest category.")
    llm = ScriptedLLM(answers=[good])
    summary = write_summary(facts, llm)
    assert (summary.source, summary.attempts, summary.text) == ("model", 1, good)
    assert "Region North: revenue 91,457 USD" in llm.prompts[0]


def test_model_gets_one_retry_naming_the_wrong_numbers(facts):
    bad = "Revenue reached 260,000 USD in September 2026, a strong month for every region and for the whole company."
    good = "Revenue reached 253,055 USD in September 2026, a strong month for the company with North in the lead overall."
    llm = ScriptedLLM(answers=[bad, good])
    summary = write_summary(facts, llm)
    assert summary.source == "model" and summary.attempts == 2
    assert "260,000" in llm.prompts[1]


def test_template_is_used_when_the_model_keeps_inventing_numbers(facts):
    bad = "Revenue reached 260,000 USD in September 2026, a strong month for every region and for the whole company."
    summary = write_summary(facts, ScriptedLLM(answers=[bad, bad]))
    assert summary.source == "template (model answer rejected)" and summary.rejected_numbers == ["260,000", "260,000"]
    assert summary.text == template_summary(facts)


def test_template_is_used_when_the_model_is_down_or_absent(facts):
    assert write_summary(facts, ScriptedLLM(answers=[RuntimeError("503")])).source == "template (model unavailable)"
    assert write_summary(facts, ScriptedLLM(answers=["", ""])).source == "template (model answer rejected)"
    assert write_summary(facts, None).source == "template"
