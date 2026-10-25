// The runtime message contract between the popup and background.ts.
import { getMessage } from "#/i18n";
import type {
  Delivered,
  Failure,
  Handlers,
  Reply,
  Request,
  RequestType,
  Respond,
  RuntimeListener,
  Send,
  UntypedHandler,
} from "#/types";

export function noAnswer(): string {
  return getMessage("errNoAnswer");
}

export function failed<T extends object>(reply: T | Failure): reply is Failure {
  return "error" in reply && Boolean(reply.error);
}

function messageOf(error: unknown): unknown {
  return typeof error === "object" && error !== null && "message" in error
    ? error.message
    : undefined;
}

export function errorText(error: unknown): string {
  const message = messageOf(error);

  return (typeof message === "string" && message) || String(error);
}

function typeOf(message: unknown): unknown {
  return typeof message === "object" && message !== null && "type" in message
    ? message.type
    : undefined;
}

// Each handler takes the request of its own type; the map type cannot express that pairing.
function handlerFor(handlers: Handlers, type: unknown): UntypedHandler | undefined {
  if (!Object.hasOwn(handlers, type as PropertyKey)) {
    return undefined;
  }

  return handlers[type as RequestType] as UntypedHandler | undefined;
}

function unknownType(type: unknown): Failure {
  const name = JSON.stringify(type);

  return { error: getMessage("errUnknownType", { type: name }) };
}

function answer(handler: UntypedHandler, message: unknown, respond: Respond): void {
  function run() {
    return handler(message);
  }

  function handleResult(result: unknown) {
    respond(result ?? {});
  }

  function handleError(error: unknown) {
    respond({ error: errorText(error) });
  }

  Promise.resolve().then(run).then(handleResult, handleError);
}

// An onMessage listener that answers every message through sendResponse and keeps the channel open.
export function messageListener(handlers: Handlers): RuntimeListener {
  return function handleRuntimeMessage(
    message: unknown,
    _sender: unknown,
    respond: Respond,
  ): boolean {
    const type = typeOf(message);
    const handler = handlerFor(handlers, type);

    if (!handler) {
      respond(unknownType(type));

      return false;
    }

    answer(handler, message, respond);

    return true;
  };
}

// Messages whose handler has side effects a second delivery would repeat.
const NOT_RETRIED: ReadonlySet<RequestType> = new Set(["openAll", "checkNow", "closeDuplicates"]);

async function sendOnce(send: Send, message: Request): Promise<Delivered | Failure> {
  try {
    const reply = await send(message);

    return reply === undefined || reply === null ? { error: noAnswer() } : { reply };
  } catch (error) {
    return { error: errorText(error) };
  }
}

// Sends message, retrying once when there is no answer or the send throws, unless NOT_RETRIED;
// failures come back as { error }.
export async function ask<M extends Request>(send: Send, message: M): Promise<Reply<M["type"]>> {
  const attempts = NOT_RETRIED.has(message.type) ? 1 : 2;
  let failure: Failure = { error: noAnswer() };

  for (let attempt = 0; attempt < attempts; attempt++) {
    const outcome = await sendOnce(send, message);

    if ("reply" in outcome) {
      // The service worker answers each request type with its reply shape.
      return outcome.reply as Reply<M["type"]>;
    }

    failure = outcome;
  }

  return failure;
}
