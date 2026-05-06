const COSTS: Record<string, [number, number]> = {
  'gpt-4o': [0.0025, 0.010],
  'gpt-4o-mini': [0.00015, 0.0006],
  'gpt-4-turbo': [0.010, 0.030],
  'gpt-3.5-turbo': [0.0005, 0.0015],
  'claude-opus-4': [0.015, 0.075],
  'claude-sonnet-4': [0.003, 0.015],
  'claude-haiku-4': [0.00025, 0.00125],
  'gemini-1.5-pro': [0.00125, 0.005],
  'gemini-1.5-flash': [0.000075, 0.0003],
}

export function calculateCost(model: string, inputTokens: number, outputTokens: number): number {
  const entry = Object.entries(COSTS)
    .sort((a, b) => b[0].length - a[0].length)
    .find(([k]) => model.startsWith(k))
  const [inputRate, outputRate] = entry ? entry[1] : [0.002, 0.002]
  return (inputTokens / 1000 * inputRate) + (outputTokens / 1000 * outputRate)
}
