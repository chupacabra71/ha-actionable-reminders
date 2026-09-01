"""A message too long to speak is caught when it is written, not at 2am.

Two reminders sat 17 and 19 characters over the spoken budget for four days,
truncating the reply hint off the end of every ask — the prompt still went out
and still sounded fine, it just stopped before telling anyone how to answer.
The engine warned on each send, into a log nobody reads at the hour reminders
fire. These pin the check that refuses the message at the keyboard instead.
"""

from __future__ import annotations

from conftest import const, reminder_mod

spoken_overrun = reminder_mod.spoken_overrun
spoken_budget = reminder_mod.spoken_budget

# What the engine appends to an actionable prompt and the author never sees.
DECORATION = max(len(p) for p in const.DEFAULT_QUESTION_PHRASES) + len(
    const.DEFAULT_RESPONSE_HINT
) + 2


def message_of(n: int) -> str:
    return "x" * n


# ── the budget ────────────────────────────────────────────────────────────────

def test_the_budget_is_the_same_for_every_reminder():
    # The event id is "ar_" + the 26-character subentry ULID, so it costs the
    # same for all of them. Believing the reminder's NAME was in there is what
    # made the first hand-audit of this wrong.
    assert spoken_budget("01M0AYYR4KNH6R1ZT1FQR2P45K") == spoken_budget("01KY08VYTNNEA7P6NPWTR76BRW")
    # And a reminder being created has no id yet — the wizard still needs a number.
    assert spoken_budget(None) == spoken_budget("01M0AYYR4KNH6R1ZT1FQR2P45K")


def test_a_long_name_does_not_shrink_the_budget():
    budget = spoken_budget()
    short, long = "Recycling", "Vacuums - Check Rollers and Filters Every Other Week"
    fits = message_of(budget - DECORATION)
    assert spoken_overrun(fits, short) == 0
    assert spoken_overrun(fits, long) == 0, "renaming a reminder buys no budget back"


# ── what counts against it ────────────────────────────────────────────────────

def test_an_actionable_message_is_measured_with_the_appended_text():
    budget = spoken_budget()
    assert spoken_overrun(message_of(budget - DECORATION), "Recycling") == 0
    assert spoken_overrun(message_of(budget - DECORATION + 1), "Recycling") == 1
    assert spoken_overrun(message_of(budget), "Recycling") == DECORATION


def test_an_announcement_is_measured_bare():
    budget = spoken_budget()
    # Nothing is appended to a non-actionable reminder — it states, it doesn't ask.
    assert spoken_overrun(message_of(budget), "Recycling", actionable=False) == 0
    assert spoken_overrun(message_of(budget + 5), "Recycling", actionable=False) == 5


def test_a_birthday_is_measured_against_its_own_longer_pool():
    # Birthdays draw from DEFAULT_BIRTHDAY_QUESTION_PHRASES, whose longest entry
    # is longer than any general one. Measuring them against the general pool
    # under-counts every birthday reminder.
    birthday_decoration = max(
        len(p) for p in const.DEFAULT_BIRTHDAY_QUESTION_PHRASES
    ) + len(const.DEFAULT_RESPONSE_HINT) + 2
    assert birthday_decoration > DECORATION, "otherwise this test proves nothing"

    at_the_line = message_of(spoken_budget() - birthday_decoration)
    assert spoken_overrun(at_the_line, "Mom's Birthday") == 0
    assert spoken_overrun(at_the_line + "x", "Mom's Birthday") == 1
    # The same text is comfortably fine for a non-birthday reminder.
    assert spoken_overrun(at_the_line + "x", "Recycling") == 0


def test_the_pool_is_chosen_case_insensitively():
    at_the_line = message_of(
        spoken_budget()
        - (max(len(p) for p in const.DEFAULT_BIRTHDAY_QUESTION_PHRASES)
           + len(const.DEFAULT_RESPONSE_HINT) + 2)
    )
    assert spoken_overrun(at_the_line + "x", "Dad's BIRTHDAY") == 1


# ── what it declines to measure ───────────────────────────────────────────────

def test_a_template_is_left_to_the_send_time_check():
    # Its rendered length isn't knowable here, and refusing to save one because
    # its SOURCE is long would block the feature that lets a message name what
    # tripped it. The engine's own warning covers this case.
    long_template = "{{ x }}" + message_of(spoken_budget() * 2)
    assert spoken_overrun(long_template, "Recycling") == 0
    assert spoken_overrun("{% set a = 1 %}" + message_of(spoken_budget() * 2), "Recycling") == 0


def test_an_empty_message_is_not_an_overrun():
    assert spoken_overrun("", "Recycling") == 0


# ── the real regression ───────────────────────────────────────────────────────

def test_the_message_that_shipped_broken_is_refused():
    # Exactly what sat in the config for four days.
    was = ("Consuela's clean water tank is empty or not seated. Fill it and "
           "reseat it. Until then her mop jobs will run as vacuum only.")
    now = ("Consuela's clean water tank is empty or not seated. Fill and "
           "reseat it, or she mops dry.")
    assert spoken_overrun(was, "Consuela - Fill Clean Water Tank") > 0
    assert spoken_overrun(now, "Consuela - Fill Clean Water Tank") == 0
