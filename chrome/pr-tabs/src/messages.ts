// The runtime message contract between the popup and background.ts.
import type { CheckResult, OpenAllResult, Summary } from "./core";

export const NO_ANSWER =
  "No answer from the PR tabs service worker. Reload the extension at chrome://extensions.";

export type Request =
  | { readonly type: "summary" }
  | { readonly type: "repos" }
  | { readonly type: "pickRepo"; readonly repo: string }
  | { readonly type: "goTo"; readonly input: string; readonly repo?: string }
  | { readonly type: "openAll" }
  | { readonly type: "checkNow" }
  | { readonly type: "closeDuplicates" };
export type RequestType = Request["type"];
export type RequestOf<K extends RequestType> = Extract<Request, { readonly type: K }>;

export interface Failure {
  readonly error: string;
}

export interface RepoChoices {
  readonly repos: readonly string[];
  readonly selected: string;
  readonly show: boolean;
}

export type GoToResult = { readonly opened: string } | { readonly focused: string };

export interface Replies {
  readonly summary: Summary;
  readonly repos: RepoChoices;
  readonly pickRepo: Readonly<Record<string, never>>;
  readonly goTo: GoToResult;
  readonly openAll: OpenAllResult;
  readonly checkNow: CheckResult;
  readonly closeDuplicates: { readonly closed: number };
}
export type Reply<K extends RequestType> = Replies[K] | Failure;

type Answer<K extends RequestType> = Reply<K> | void;
export type Handler<K extends RequestType> = (
  message: RequestOf<K>,
) => Answer<K> | Promise<Answer<K>>;
export type Handlers = { readonly [K in RequestType]?: Handler<K> };
export type Send = (message: Request) => Promise<unknown>;
type Respond = (answer: unknown) => void;

// A reply failed when it carries an error text.
export function failed<T extends object>(reply: T | Failure): reply is Failure {
  return "error" in reply && Boolean(reply.error);
}

function messageOf(error: unknown): unknown {
  return typeof error === "object" && error !== null && "message" in error
    ? error.message
    : undefined;
}

// The error's message, else the error as text.
export function errorText(error: unknown): string {
  const message = messageOf(error);
  return (typeof message === "string" && message) || String(error);
}

function typeOf(message: unknown): unknown {
  return typeof message === "object" && message !== null && "type" in message
    ? message.type
    : undefined;
}

type AnyHandler = (request: unknown) => unknown;

// Each handler takes the request of its own type; the map type cannot express that pairing.
function handlerFor(handlers: Handlers, type: unknown): AnyHandler | undefined {
  if (!Object.hasOwn(handlers, type as PropertyKey)) {
    return undefined;
  }
  return handlers[type as RequestType] as AnyHandler | undefined;
}

function unknownType(type: unknown): Failure {
  const name = JSON.stringify(type);
  return { error: `Unknown message type ${name}. Reload the extension at chrome://extensions.` };
}

// Runs the handler and answers with its result, {} for none, or { error }.
function answer(handler: AnyHandler, message: unknown, respond: Respond): void {
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
export function messageListener(handlers: Handlers) {
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
export const NOT_RETRIED: ReadonlySet<RequestType> = new Set([
  "openAll",
  "checkNow",
  "closeDuplicates",
]);

// The reply to one send, or the failure text when there is none.
async function sendOnce(send: Send, message: Request): Promise<{ reply: unknown } | Failure> {
  try {
    const reply = await send(message);
    return reply === undefined || reply === null ? { error: NO_ANSWER } : { reply };
  } catch (error) {
    return { error: errorText(error) };
  }
}

// Sends message, retrying once when there is no answer or the send throws, unless NOT_RETRIED;
// failures come back as { error }.
export async function ask<M extends Request>(send: Send, message: M): Promise<Reply<M["type"]>> {
  const attempts = NOT_RETRIED.has(message.type) ? 1 : 2;
  let failure: Failure = { error: NO_ANSWER };
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
