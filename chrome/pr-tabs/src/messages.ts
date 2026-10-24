// The runtime message contract between the popup and background.ts.

export const NO_ANSWER = "No answer from the PR tabs service worker. Reload the extension at chrome://extensions.";

export interface Message {
  type?: unknown;
  [field: string]: unknown;
}

export type Handler = (message: Message) => unknown;

// An onMessage listener that answers every message through sendResponse and keeps the channel open.
export function messageListener(handlers: Record<string, Handler>) {
  return (message: Message | undefined, _sender: unknown, sendResponse: (answer: unknown) => void): boolean => {
    const type = message?.type;
    if (!Object.hasOwn(handlers, type as PropertyKey)) {
      sendResponse({
        error: `Unknown message type ${JSON.stringify(type)}. Reload the extension at chrome://extensions.`,
      });
      return false;
    }
    Promise.resolve()
      .then(() => handlers[type as string]!(message!))
      .then(
        (result) => sendResponse(result ?? {}),
        (error: unknown) => sendResponse({ error: (error as Error | undefined)?.message || String(error) }),
      );
    return true;
  };
}

// Messages whose handler has side effects a second delivery would repeat.
export const NOT_RETRIED = new Set(["openAll", "checkNow", "closeDuplicates"]);

// Sends message, retrying once when there is no answer or the send throws, unless NOT_RETRIED; failures come back as { error }.
export async function ask<T = Record<string, unknown>>(
  send: (message: Message) => Promise<unknown>,
  message: Message,
): Promise<T | { error: string }> {
  let failure = NO_ANSWER;
  const attempts = NOT_RETRIED.has(message?.type as string) ? 1 : 2;
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      const reply = await send(message);
      if (reply !== undefined && reply !== null) return reply as T;
      failure = NO_ANSWER;
    } catch (error) {
      failure = (error as Error | undefined)?.message || String(error);
    }
  }
  return { error: failure };
}
