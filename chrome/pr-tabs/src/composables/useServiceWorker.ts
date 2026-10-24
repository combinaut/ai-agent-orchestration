import { ask as askWith, type Reply, type Request, type Send } from "#/messages";

export type Ask = <M extends Request>(message: M) => Promise<Reply<M["type"]>>;

function sendToServiceWorker(message: Request): Promise<unknown> {
  return chrome.runtime.sendMessage(message);
}

// Asks the service worker through chrome.runtime.sendMessage, with the retry rules of messages.ts.
export function useServiceWorker(send: Send = sendToServiceWorker): { ask: Ask } {
  function ask<M extends Request>(message: M): Promise<Reply<M["type"]>> {
    return askWith(send, message);
  }

  return { ask };
}
