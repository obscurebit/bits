"""Request options for the verified NVIDIA generation model."""


def completion_options(model: str) -> dict:
    # Reasoning tokens can exhaust the short fiction / JSON output budgets.
    if model.startswith("nvidia/nemotron-3-"):
        return {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    return {}
