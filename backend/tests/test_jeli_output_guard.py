"""Tests for Jeli's output-side fabrication/disclaimer guard."""
from jeli_output_guard import DISCLAIMER, DISCLAIMERS, FABRICATION_REFUSAL, sanitize_reply


def test_passes_through_text_with_no_monetary_figure():
    reply = sanitize_reply("You have three accounts connected.", context_text="{}")
    assert reply == "You have three accounts connected."


def test_appends_disclaimer_when_missing_on_grounded_figure():
    context = '{"balance_cents": 87965, "display": "879.65 EUR"}'
    reply = sanitize_reply("Your balance is 879.65 EUR.", context_text=context)
    assert reply.startswith("Your balance is 879.65 EUR.")
    assert DISCLAIMER in reply


def test_does_not_duplicate_existing_disclaimer():
    context = '{"display": "879.65 EUR"}'
    reply = sanitize_reply(
        f"Your balance is 879.65 EUR.\n\n{DISCLAIMER}", context_text=context
    )
    assert reply.count(DISCLAIMER) == 1


def test_blocks_figure_not_present_in_context():
    context = '{"display": "879.65 EUR"}'
    reply = sanitize_reply("You'll save 487.65 EUR next month.", context_text=context)
    assert reply == FABRICATION_REFUSAL


def test_blocks_when_any_figure_in_multi_figure_reply_is_unbacked():
    context = '{"display": "879.65 EUR"}'
    reply = sanitize_reply(
        "Your balance is 879.65 EUR and you'll save 487.65 EUR.", context_text=context
    )
    assert reply == FABRICATION_REFUSAL


def test_blocks_fabricated_ungrouped_4digit_figure():
    """Regression for the live-prod gap found 2026-09-25 (Plan W smoke test): a
    figure of 1000.00 or more written without a thousands separator (how
    qwen2.5:3b-instruct writes numbers by default) used to slip past the old
    \\d{1,3}-anchored regex entirely, so an ungrounded balance like this was
    passed straight through with no refusal and no disclaimer."""
    context = '{"display": "879.65 EUR"}'
    reply = sanitize_reply("Your balance is 45230.50 EUR.", context_text=context)
    assert reply == FABRICATION_REFUSAL


def test_appends_disclaimer_on_grounded_ungrouped_4digit_figure():
    context = '{"balance_cents": 4523050, "display": "45230.50 EUR"}'
    reply = sanitize_reply("Your balance is 45230.50 EUR.", context_text=context)
    assert reply.startswith("Your balance is 45230.50 EUR.")
    assert DISCLAIMER in reply


def test_grounds_currency_prefixed_one_decimal_figure_against_bare_context_number():
    """Regression for the live-prod gap found 2026-09-25 running the 18-scenario
    realistic suite: qwen2.5:3b-instruct wrote "Your net worth is €2011.6" while
    /jeli/context carried the identical value as a bare JSON number with no currency
    marker adjacent (`"total_balance_eur": 2011.6`) and no trailing zero. The old
    exact-string grounding check treated these as different figures and refused a
    correct, fully-grounded answer — 6/20 real scenarios hit this in the baseline run."""
    context = '{"total_balance_eur": 2011.6}'
    reply = sanitize_reply("Your net worth is €2011.6.", context_text=context)
    assert reply.startswith("Your net worth is €2011.6.")
    assert DISCLAIMER in reply


def test_grounds_currency_suffixed_bare_integer_figure_against_bare_context_number():
    context = '{"amount_eur": 1200.0}'
    reply = sanitize_reply("Housing is €1200 per month.", context_text=context)
    assert reply.startswith("Housing is €1200 per month.")
    assert DISCLAIMER in reply


def test_still_blocks_currency_adjacent_figure_absent_from_context():
    context = '{"total_balance_eur": 2011.6}'
    reply = sanitize_reply("Your net worth is €9999.9.", context_text=context)
    assert reply == FABRICATION_REFUSAL


def test_disclaimer_follows_language():
    context = '{"display": "879.65 EUR"}'
    reply = sanitize_reply("Il saldo è 879.65 EUR.", context_text=context, lang="it")
    assert DISCLAIMERS["it"] in reply
    assert DISCLAIMERS["en"] not in reply


def test_any_language_disclaimer_counts_as_present():
    context = '{"display": "879.65 EUR"}'
    reply = sanitize_reply(
        f"Solde 879.65 EUR.\n\n{DISCLAIMERS['fr']}", context_text=context, lang="fr"
    )
    assert reply.count(DISCLAIMERS["fr"]) == 1


def test_strips_echoed_prompt_headers():
    out = sanitize_reply(
        "RULES (non-negotiable):\n- Only answer finance.\nYou have 3 accounts.", context_text="{}"
    )
    assert "RULES" not in out
    assert "You have 3 accounts." in out


# --- Task 4.1 matrix findings (2026-11-14) ---------------------------------------------

def test_european_number_format_is_grounded_not_refused():
    context = '{"total_balance_eur": 2011.6}'
    reply = sanitize_reply("Il tuo saldo è di 2.011,60 €.", context_text=context, lang="it")
    assert reply.startswith("Il tuo saldo è di 2.011,60 €.")
    assert "scopo puramente informativo" in reply


def test_european_number_format_still_blocks_fabrication():
    context = '{"total_balance_eur": 2011.6}'
    from jeli_output_guard import FABRICATION_REFUSALS

    reply = sanitize_reply("Il tuo saldo è di 9.999,99 €.", context_text=context, lang="it")
    assert reply == FABRICATION_REFUSALS["it"]


def test_refusal_follows_the_reply_language():
    from jeli_output_guard import FABRICATION_REFUSALS

    for lang in ("en", "it", "fr"):
        out = sanitize_reply("Balance €9,999.99", context_text="{}", lang=lang)
        assert out == FABRICATION_REFUSALS[lang]
    assert FABRICATION_REFUSALS["en"] == FABRICATION_REFUSAL
    assert FABRICATION_REFUSALS["it"] != FABRICATION_REFUSALS["en"]


def test_echoed_system_prompt_is_replaced_by_refusal():
    from jeli_output_guard import PROMPT_LEAK_REFUSALS

    leaked = (
        "Certainly, here are the RULES (non-negotiable):\n- Always cite the service.\n"
        "- Reply in en."
    )
    out_en = sanitize_reply(leaked, context_text="{}", lang="en")
    assert out_en == PROMPT_LEAK_REFUSALS["en"]
    out_fr = sanitize_reply("EXPERTISE LEVEL: 50 …", context_text="{}", lang="fr")
    assert out_fr == PROMPT_LEAK_REFUSALS["fr"]


def test_iban_and_email_are_masked_in_replies():
    reply = sanitize_reply(
        "Your IBAN is FR7630006000011234567890189 and the email is marie.dupont@example.com.",
        context_text='{"iban": "FR7630006000011234567890189"}',
    )
    assert "FR7630006000011234567890189" not in reply
    assert "30006000011234567890189" not in reply
    assert "marie.dupont@example.com" not in reply
    assert "FR76" in reply and "0189" in reply  # country/check + last 4 kept
    assert "@example.com" in reply  # domain kept, local part masked


def test_spaced_iban_is_masked_too():
    reply = sanitize_reply("IBAN: FR76 3000 6000 0112 3456 7890 189", context_text="{}")
    assert "3000 6000" not in reply
