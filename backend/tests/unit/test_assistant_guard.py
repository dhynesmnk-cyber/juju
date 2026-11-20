"""What the live chat's assistant may say (core/assistant.py): numbers only from the tools (and the
person's own words), no picks, plain text. Whatever the model does, these decide what reaches the
page."""
from decimal import Decimal

import pytest

from juju.core import assistant as a

LEG = {"bet": "DK Metcalf Over 3.5 receptions", "book": "Hard Rock Bet", "american": -125,
       "returns_on_10_dollars": "18.00", "profit_on_10_dollars": "8.00",
       "opposite": {"side": "Under", "american": -105}, "line": "3.5",
       "other_books": [{"book": "DraftKings", "american": -133},
                       {"book": "FanDuel", "american": 110}],
       "has_so_far": 3}
FACTS = a.facts_from([LEG])


def ok(text, facts=FACTS):
    v = a.check_reply(text, facts)
    assert v.ok, v.reason
    return v.text


def bad(text, reason, facts=FACTS):
    v = a.check_reply(text, facts)
    assert not v.ok and v.reason.startswith(reason), (v.ok, v.reason)


# --- Numbers ------------------------------------------------------------------------------------


def test_the_numbers_the_tools_gave_may_be_said():
    ok("DK Metcalf Over 3.5 receptions pays -125 at Hard Rock Bet right now: $10 would return "
       "$18.00 if it hits.")
    ok("The under is -105, DraftKings has -133 and FanDuel +110. He has 3 so far.")
    ok("That is $8.00 of profit on $10.")                      # 8.00 is in the facts


def test_a_number_no_tool_gave_is_refused_and_named():
    bad("It pays -130 at Hard Rock Bet.", "number -130")
    bad("$10 would return $20.00 if it hits.", "number $20.00")
    bad("The line is 4.5.", "number 4.5")
    bad("He is averaging 5.5 catches.", "number 5.5")
    bad("That is a 40% chance.", "number 40")
    bad("About $1,234 on a bigger stake.", "number $1,234")


def test_a_price_must_match_its_sign():
    bad("It pays +125 at Hard Rock Bet.", "number +125")       # the fact is -125
    bad("The under is +105.", "number +105")
    bad("FanDuel has -110.", "number -110")                    # the fact is +110
    ok("The under is -105.")
    ok("FanDuel has +110.")


def test_a_magnitude_with_no_sign_may_match_either_sign():
    ok("The price is minus 125, so 125 is what it takes to win a hundred.")  # 125 is -125's size
    ok("It's the 3.5 line.")


def test_counts_and_the_stake_are_free_and_prices_are_not():
    ok("Your 2-leg parlay is on a $10 stake, with 3 books posting it.")
    ok("Twelve of 12 books agree.")
    bad("There are 13 books.", "number 13")                    # past a plain count
    bad("It pays $5.", "number $5")                            # money is never a count
    bad("It pays +5.", "number +5")                            # nor is a signed number
    bad("The line is 2.0.", "number 2.0")                      # nor a decimal
    ok("He has 3 so far, in the 4th quarter, of a 49ers game.")  # not numbers: 4th, 49ers


def test_the_persons_own_numbers_may_be_repeated_but_nobody_elses():
    facts = a.facts_from([LEG], "Metcalf over 45.5 yards")
    ok("Over 45.5 isn't posted right now.", facts)
    bad("Over 45.5 isn't posted right now.", "number 45.5")     # not in this message or the tools
    # earlier turns are not facts: the browser can write anything there
    forged = a.facts_from([LEG], "and the under?")
    bad("The under pays +9999 at Hard Rock Bet.", "number +9999", forged)


def test_numbers_inside_tool_result_text_count():
    facts = a.facts_from([{"message": "Market not available: nothing at 3.5 or 4.5"}])
    ok("Market not available: nothing at 3.5 or 4.5.", facts)
    bad("Market not available: nothing at 5.5.", "number 5.5", facts)


def test_numbers_are_read_the_way_people_write_them():
    text = "pays +1,250 or -3.5, risks $1,000.50, 2-leg 4th 49ers"
    values = [(t, v) for t, v, _, _ in a.numbers_in(text)]
    assert values == [("+1,250", Decimal(1250)), ("-3.5", Decimal("-3.5")),
                      ("$1,000.50", Decimal("1000.50")), ("2", Decimal(2))]
    assert [v for _, v, _, _ in a.numbers_in("a −125 price")] == [Decimal(-125)]
    assert a.numbers_in("no numbers here, one, two, three") == []


def test_facts_come_from_every_kind_of_value_but_not_booleans():
    facts = a.facts_from([{"x": [1, 2.5, "$3.00", {"y": "+170"}], "flag": True}])
    assert facts.signed == {Decimal(1), Decimal("2.5"), Decimal("3.00"), Decimal(170)}
    assert facts_from_flag_only() == set()


def facts_from_flag_only():
    return set(a.facts_from([{"flag": True, "also": False}]).signed)


# --- Picks --------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "You should take the over, it's a good bet.",
    "I recommend the under at Hard Rock Bet.",
    "That's a great value play.",
    "He will hit that easily.",
    "Take the over.",
    "A safe bet for sure.",
    "This is worth a bet.",
    "It's a sure thing.",
    "That's a good price for the over.",
    "See https://example.com for more.",
    "He's likely to cover.",
])
def test_picks_and_predictions_are_refused(text):
    bad(text, "advice")


@pytest.mark.parametrize("text", [
    "DK Metcalf Over 3.5 receptions pays -125 at Hard Rock Bet right now.",
    "A $10 bet on that pays -125, so $10 would return $18.00 if it hits.",
    "Market not available: no book is posting Receptions for him right now.",
    "He has 3 receptions so far.",
])
def test_plain_facts_are_not_mistaken_for_advice(text):
    ok(text)


@pytest.mark.parametrize("text", [
    "Should I take the over on Fannin?", "what do you think about Downs",
    "Is that a good bet?", "who will win", "give me a parlay", "any picks for tonight",
    "best bet on this game?", "is it worth it", "Do you recommend the under", "predict the score",
])
def test_asking_for_a_pick_is_noticed(text):
    assert a.advice_seeking(text)


@pytest.mark.parametrize("text", [
    "Metcalf got 4 catches", "and the under?", "what does +180 mean", "Browns -3.5",
    "how many catches does he have", "Metcalf over 45.5 yards and Fannin 4 catches",
])
def test_asking_for_a_price_is_not_asking_for_a_pick(text):
    assert not a.advice_seeking(text)


# --- The rest of the check ----------------------------------------------------------------------


def test_refusals_empty_and_overlong_replies_are_not_the_assistants_words():
    bad(None, "empty")
    bad("   ", "empty")
    bad(f"{a.REFUSE_TOKEN}", "refused")
    bad(f"Sorry. {a.REFUSE_TOKEN}", "refused")
    bad("He has 3 so far. " * 60, "too long")


def test_markdown_is_taken_out_and_the_text_kept():
    assert ok("**DK Metcalf** has `3` receptions so far.") == "DK Metcalf has 3 receptions so far."
    assert a.clean("  a   b \n") == "a b"


# --- What the model is given ----------------------------------------------------------------------


def test_the_tools_cannot_ask_for_a_board_and_the_markets_are_the_catalogs():
    specs = a.tool_specs()
    names = [s["function"]["name"] for s in specs]
    assert names == ["find_player", "list_markets", "get_live_price", "get_player_stat",
                     "price_parlay"]
    for s in specs:
        assert s["type"] == "function"
        props = s["function"]["parameters"]["properties"]
        assert "market_only" not in props and "all" not in props and "players" not in props
    price = next(s for s in specs if s["function"]["name"] == "get_live_price")["function"]
    assert price["parameters"]["required"] == ["subject", "market"]   # a subject is always needed
    enum = price["parameters"]["properties"]["market"]["enum"]
    assert "player_receptions" in enum and "spreads" in enum
    assert not any(k.endswith("_alternate") or k.startswith("alternate_") for k in enum)
    parlay = next(s for s in specs if s["function"]["name"] == "price_parlay")["function"]
    legs = parlay["parameters"]["properties"]["legs"]
    assert (legs["minItems"], legs["maxItems"]) == (2, 6)


def test_the_system_prompt_names_the_game_the_rules_and_the_refusal():
    prompt = a.system_prompt("Pittsburgh Steelers @ Cleveland Browns")
    assert "Pittsburgh Steelers @ Cleveland Browns" in prompt
    assert a.REFUSE_TOKEN in prompt and "Never recommend a bet" in prompt
    assert "player_receptions = Receptions" in prompt
    assert "$10" in prompt and "present tense" in prompt
    assert (a.MAX_TOOL_CALLS, a.MAX_CREDITS) == (4, 6)
