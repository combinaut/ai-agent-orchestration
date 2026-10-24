import { ask as askWith } from "#/messages";
import type { Reply, Request, Send, ServiceWorker } from "#/types";

function sendToServiceWorker(message: Request): Promise<unknown> {
  return chrome.runtime.sendMessage(message);
}

// Asks the service worker through chrome.runtime.sendMessage, with the retry rules of messages.ts.
export function useServiceWorker(send: Send = sendToServiceWorker): ServiceWorker {
  function ask<M extends Request>(message: M): Promise<Reply<M["type"]>> {
    return askWith(send, message);
  }

  return { ask };
}
