"use client";

import Link from "next/link";
import { ArrowUpRight, MessageSquareText } from "lucide-react";
import { useEffect, useState } from "react";

import { useSession, useToast } from "@/components/providers";
import { Badge, Card } from "@/components/ui";
import { api, errorMessage, useApi } from "@/lib/use-api";

import { Composer, MessageList, prettyNumber, useStickToBottom, type ChannelMessage, type ChannelOption, type OptOut, type SendResult } from "./messaging-kit";

type RecordNumber = { number: string; pretty: string; label: string; opted_out: OptOut; whatsapp_window_until: string | null };

type RecordMessagesData = {
  numbers: RecordNumber[];
  messages: ChannelMessage[];
  unread: number;
  channels: ChannelOption[];
  can_send: boolean;
};

/**
 * SMS and WhatsApp conversation with a contact, lead or account, with a composer.
 * Renders nothing when the workspace's plan doesn't include messaging.
 */
export function RecordMessages({ recordType, recordId }: { recordType: "contact" | "lead" | "account"; recordId: string }) {
  const { has, can } = useSession();
  const toast = useToast();
  const enabled = has("messaging");
  const data = useApi<RecordMessagesData>(enabled ? `/messaging/records/${recordType}/${recordId}` : null);
  const [selected, setSelected] = useState<string | null>(null);
  const messages = data.data?.messages ?? [];
  const scroller = useStickToBottom(messages.length);

  const numbers = data.data?.numbers ?? [];
  useEffect(() => {
    if (!selected && numbers.length) setSelected(numbers[0].number);
  }, [numbers, selected]);

  // Opening the record reads its messages.
  const unread = data.data?.unread ?? 0;
  useEffect(() => {
    if (!unread || !enabled) return;
    const pending = new Set(messages.filter((m) => m.direction === "inbound" && !m.is_read).map((m) => m.number).filter(Boolean) as string[]);
    pending.forEach((number) => void api.post("/messaging/thread/read", { number }).catch(() => undefined));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unread, enabled]);

  if (!enabled) return null;

  const current = numbers.find((n) => n.number === selected) ?? null;
  const anyReady = (data.data?.channels ?? []).some((c) => c.ready);
  // Without SMS or WhatsApp set up, and nothing in the history, there's nothing to show.
  if (data.data && !anyReady && !messages.length) return null;

  function appended(message: ChannelMessage) {
    data.setData((value) => (value ? { ...value, messages: [...value.messages, message] } : value));
  }

  async function retry(message: ChannelMessage) {
    try {
      const result = await api.post<SendResult>(`/messaging/messages/${message.id}/retry`);
      appended(result.message);
      if (!result.ok) toast(result.error ?? "Still not delivered.", "danger");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  return (
    <Card
      title="Messages"
      icon={<MessageSquareText size={16} />}
      actions={
        current && messages.length ? (
          <Link className="ui-btn ui-btn-ghost ui-btn-sm" href={`/integrations/messages?number=${encodeURIComponent(current.number)}`}>
            Open inbox <ArrowUpRight size={13} />
          </Link>
        ) : null
      }
    >
      {data.error ? <p className="ui-small" style={{ margin: 0, color: "var(--danger)" }}>{data.error}</p> : null}
      {!data.data ? (
        <p className="ui-muted ui-small" style={{ margin: 0 }}>Loading…</p>
      ) : (
        <div className="imsg-record">
          {numbers.length > 1 ? (
            <div className="imsg-numbers" role="radiogroup" aria-label="Number">
              {numbers.map((n) => (
                <button key={n.number} type="button" role="radio" aria-checked={selected === n.number} className={`int-chip${selected === n.number ? " on" : ""}`} onClick={() => setSelected(n.number)}>
                  {n.label} · {n.pretty}
                  {n.opted_out ? <Badge tone="danger">Opted out</Badge> : null}
                </button>
              ))}
            </div>
          ) : null}
          {messages.length ? (
            <div className="imsg-record-thread" ref={scroller}>
              <MessageList messages={messages} showNumbers={numbers.length > 1} onRetry={can("records.write") ? (m) => void retry(m) : undefined} />
            </div>
          ) : (
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              {numbers.length
                ? anyReady
                  ? `No messages with ${prettyNumber(numbers[0].number)} yet.`
                  : "No messages yet."
                : `Add a phone number to this ${recordType} to text them.`}
            </p>
          )}
          {current && data.data.can_send ? (
            <Composer
              compact
              number={current.number}
              channels={data.data.channels}
              whatsappWindowUntil={current.whatsapp_window_until}
              optedOut={current.opted_out}
              record={{ type: recordType, id: recordId }}
              onSent={appended}
            />
          ) : null}
        </div>
      )}
    </Card>
  );
}

export default RecordMessages;
