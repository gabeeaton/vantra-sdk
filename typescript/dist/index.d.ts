export interface InitOptions {
    apiKey: string;
    project: string;
    enabled?: boolean;
    captureIo?: boolean;
    patchOpenAI?: boolean;
    patchAnthropic?: boolean;
}
export declare function init(options: InitOptions): void;
export declare function trace<T extends (...args: unknown[]) => unknown>(fn: T, options?: {
    name?: string;
    promptVersion?: string;
}): T;
export interface SpanContext {
    setOutput(data: unknown): void;
    setTokens(inputTokens: number, outputTokens: number): void;
}
export declare function span<T>(name: string, fn: (ctx: SpanContext) => Promise<T> | T, options?: {
    kind?: string;
    model?: string;
    metadata?: Record<string, unknown>;
}): Promise<T>;
