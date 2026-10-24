import type { Reply, Request, RequestType } from "#/types";
import type { FakeAsk, FakeReplies } from "#test/types";

export function fakeAsk(replies: FakeReplies): FakeAsk {
  const sent: Request[] = [];

  function replyFor<K extends RequestType>(type: K): Reply<K> {
    return replies[type] ?? { error: `no reply for ${type}` };
  }

  async function ask<M extends Request>(message: M): Promise<Reply<M["type"]>> {
    sent.push(message);

    return replyFor<M["type"]>(message.type);
  }

  return { ask, sent };
}
