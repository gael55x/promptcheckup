"""A small billing-support bot with fixed answers, so only the prompt changes."""

ANSWERS = {
    "charged twice": (
        (
            "A duplicate charge is usually a pending authorisation that drops off "
            "within 3-5 business days."
        ),
        (
            "Policy 4.2 (duplicate charges): confirmed duplicates are refunded in "
            "full within 5 business days."
        ),
    ),
    "refund": (
        ("Refunds go back to the original payment method once approved."),
        (
            "Policy 6.1 (refunds): annual plans are refundable within 14 days of "
            "purchase; monthly plans are not refundable."
        ),
    ),
    "cancel": (
        (
            "You can cancel any time from Settings > Billing; access continues to "
            "the end of the period."
        ),
        (
            "Policy 3.4 (cancellation): cancelling stops renewal but does not "
            "refund the current period."
        ),
    ),
    "invoice": (
        ("Invoices are available under Settings > Billing > Invoices as PDFs."),
        (
            "Policy 2.3 (invoices): invoices can be reissued with a new company "
            "name or tax ID on request."
        ),
    ),
    "VAT": (
        ("VAT is added based on your billing country."),
        ("Policy 5.2 (tax): businesses with a valid VAT ID are charged 0% under reverse charge."),
    ),
    "card declined": (
        ("Declines usually come from the bank; try another card or contact your bank."),
        ("Policy 4.5 (failed payments): we retry 3 times over 7 days before pausing the account."),
    ),
}


def run(case, prompts):
    question = case["question"]
    topic = next(key for key in ANSWERS if key in question)
    general, policy = ANSWERS[topic]
    answer = f"Thanks for reaching out about this. {general}"
    if "cite the relevant policy" in prompts["prompts/system.md"]:
        answer += f" {policy}"
    return {"answer": answer}
