# (input_cost_per_1k, output_cost_per_1k) in USD
COSTS = {
    "gpt-4o": (0.0025, 0.010),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4-turbo": (0.010, 0.030),
    "gpt-3.5-turbo": (0.0005, 0.0015),
    "claude-opus-4": (0.015, 0.075),
    "claude-sonnet-4": (0.003, 0.015),
    "claude-haiku-4": (0.00025, 0.00125),
    "gemini-1.5-pro": (0.00125, 0.005),
    "gemini-1.5-flash": (0.000075, 0.0003),
}

def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    # Sort by key length descending so "gpt-4o-mini" matches before "gpt-4o"
    costs = next(
        (v for k, v in sorted(COSTS.items(), key=lambda x: -len(x[0])) if model.startswith(k)),
        (0.002, 0.002)
    )
    return (input_tokens / 1000 * costs[0]) + (output_tokens / 1000 * costs[1])
