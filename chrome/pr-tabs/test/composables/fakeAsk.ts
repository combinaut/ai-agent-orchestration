import type { Ask, Reply, Request, RequestType } from "#/types";

export type FakeReplies = { [K in RequestType]?: Reply<K> };

export function fakeAsk(replies: FakeReplies) {
  const sent: Request[] = [];

  function replyFor<K extends RequestType>(type: K): Reply<K> {
    return replies[type] ?? { error: `no reply for ${type}` };
  }

  async function ask<M extends Request>(message: M): Promise<Reply<M["type"]>> {
    sent.push(message);
    return replyFor<M["type"]>(message.type);
  }

  return { ask: ask satisfies Ask, sent };
}
