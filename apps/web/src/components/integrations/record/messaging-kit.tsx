"use client";

import {
  AlertCircle,
  Check,
  CheckCheck,
  Clock,
  FileText,
  MapPin,
  MessageCircle,
  MessageSquareText,
  RotateCcw,
  Send,
} from "lucide-react";
import { Fragment, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { useToast } from "@/components/providers";
import { Alert, Field } from "@/components/ui";
import { dateTime, relativeTime } from "@/lib/format";
import { api, errorMessage } from "@/lib/use-api";

/* Shapes from /messaging/* (apps/api/app/api/routes/int_messaging.py). */

export type ChannelKey = "sms" | "whatsapp";

export type MessageMedia = {
  index: number;
  type: string;
  mime_type?: string | null;
  caption?: string | null;
  filename?: string | null;
  name?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  address?: string | null;
};

export type ChannelMessage = {
  id: string;
  channel: ChannelKey;
  direction: "inbound" | "outbound";
  number: string | null;
  from_number: string | null;
  to_number: string | null;
  body: string;
  media: MessageMedia[];
  template_name: string | null;
  status: "queued" | "sent" | "delivered" | "read" | "failed" | "received";
  error: string | null;
  is_read: boolean;
  user_id: string | null;
  user_name: string | null;
  contact_id: string | null;
  lead_id: string | null;
  account_id: string | null;
  created_at: string;
};

export type WaTemplate = {
  name: string;
  language: string;
  category: string | null;
  body: string;
  body_params: string[];
  header: { format: string | null; text: string | null; params: string[] } | null;
  parameter_format?: string;
  footer?: string | null;
};

export type ChannelOption = {
  channel: ChannelKey;
  key: string;
  label: string;
  ready: boolean;
  from: string | null;
  templates?: WaTemplate[];
};

export type RecordRef = { type: string; id: string; label: string; href: string; kind: string; suggested?: boolean };

export type OptOut = { reason: string; note?: string | null; at: string } | null;

export type SendResult = { ok: boolean; message: ChannelMessage; error: string | null };

export const CHANNEL_LABEL: Record<ChannelKey, string> = { sms: "SMS", whatsapp: "WhatsApp" };

export function ChannelChip({ channel }: { channel: ChannelKey }) {
  return (
    <span className={`imsg-channel ${channel}`}>
      {channel === "whatsapp" ? <MessageCircle size={11} aria-hidden /> : <MessageSquareText size={11} aria-hidden />}
      {CHANNEL_LABEL[channel]}
    </span>
  );
}

/** "+12145550100" → "+1 214 555 0100" (other countries are shown as stored). */
export function prettyNumber(number: string | null | undefined) {
  if (!number) return "";
  const digits = number.replace(/\D/g, "");
  if (digits.length === 11 && digits.startsWith("1")) return `+1 ${digits.slice(1, 4)} ${digits.slice(4, 7)} ${digits.slice(7)}`;
  return number;
}

const GSM = /^[@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !"#¤%&'()*+,\-./0-9:;<=>?¡A-ZÄÖÑÜ§¿a-zäöñüà^{}\\[~\]|€]*$/;

/** Rough SMS segment count (GSM-7: 160/153 characters, otherwise 70/67). */
export function smsSegments(text: string) {
  if (!text) return { segments: 0, perSegment: 160, unicode: false };
  const unicode = !GSM.test(text);
  const single = unicode ? 70 : 160;
  const multi = unicode ? 67 : 153;
  const length = unicode ? Array.from(text).length : text.length + (text.match(/[\^{}\\[~\]|€]/g)?.length ?? 0);
  return { segments: length <= single ? 1 : Math.ceil(length / multi), perSegment: length <= single ? single : multi, unicode };
}

function dayLabel(value: string) {
  const d = new Date(value);
  const today = new Date();
  const yesterday = new Date(Date.now() - 86_400_000);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", year: d.getFullYear() === today.getFullYear() ? undefined : "numeric" });
}

function StatusIcon({ message }: { message: ChannelMessage }) {
  if (message.direction === "inbound") return null;
  const map = {
    queued: { icon: <Clock size={12} />, label: "Sending" },
    sent: { icon: <Check size={12} />, label: "Sent" },
    delivered: { icon: <CheckCheck size={12} />, label: "Delivered" },
    read: { icon: <CheckCheck size={12} className="imsg-read" />, label: "Read" },
    failed: { icon: <AlertCircle size={12} />, label: "Not delivered" },
    received: { icon: null, label: "" },
  } as const;
  const info = map[message.status] ?? map.sent;
  return (
    <span className={`imsg-status status-${message.status}`} title={info.label}>
      {info.icon}
      <span>{info.label}</span>
    </span>
  );
}

function Media({ message, item }: { message: ChannelMessage; item: MessageMedia }) {
  const url = `/api/aiva/messaging/messages/${message.id}/media/${item.index}`;
  if (item.type === "location") {
    const query = item.latitude != null && item.longitude != null ? `${item.latitude},${item.longitude}` : encodeURIComponent(item.address ?? "");
    return (
      <a className="imsg-attachment" href={`https://www.google.com/maps?q=${query}`} target="_blank" rel="noopener noreferrer">
        <MapPin size={14} /> {item.name || item.address || "Open the location"}
      </a>
    );
  }
  const mime = item.mime_type ?? "";
  if (item.type === "image" || item.type === "sticker" || mime.startsWith("image/")) {
    return (
      <a href={url} target="_blank" rel="noopener noreferrer" className="imsg-image">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={url} alt={item.caption || "Picture"} loading="lazy" />
      </a>
    );
  }
  if (item.type === "audio" || mime.startsWith("audio/")) {
    return <audio className="imsg-audio" controls preload="none" src={url} />;
  }
  return (
    <a className="imsg-attachment" href={url} target="_blank" rel="noopener noreferrer">
      <FileText size={14} /> {item.filename || (item.type === "video" ? "Video" : "Attachment")}
    </a>
  );
}

/** Messages as bubbles, grouped by day, oldest first. */
export function MessageList({
  messages,
  showNumbers,
  onRetry,
  empty = "No messages yet.",
}: {
  messages: ChannelMessage[];
  showNumbers?: boolean;
  onRetry?: (message: ChannelMessage) => void;
  empty?: string;
}) {
  if (!messages.length) return <p className="imsg-empty">{empty}</p>;
  let lastDay = "";
  return (
    <div className="imsg-list">
      {messages.map((message) => {
        const day = dayLabel(message.created_at);
        const separator = day !== lastDay;
        lastDay = day;
        const out = message.direction === "outbound";
        return (
          <Fragment key={message.id}>
            {separator ? <div className="imsg-day">{day}</div> : null}
            <div className={`imsg-row ${out ? "out" : "in"}`}>
              <div className={`imsg-bubble ${out ? "out" : "in"}${message.status === "failed" ? " failed" : ""}`}>
                {message.template_name ? <div className="imsg-template">Template · {message.template_name}</div> : null}
                {message.body ? <div className="imsg-text">{message.body}</div> : null}
                {message.media.map((item) => (
                  <Media key={item.index} message={message} item={item} />
                ))}
              </div>
              <div className="imsg-meta">
                <ChannelChip channel={message.channel} />
                <time dateTime={message.created_at} title={dateTime(message.created_at)}>
                  {new Date(message.created_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
                </time>
                {out && message.user_name ? <span>· {message.user_name}</span> : null}
                {showNumbers && message.number ? <span>· {prettyNumber(message.number)}</span> : null}
                <StatusIcon message={message} />
              </div>
              {message.status === "failed" && message.error ? (
                <div className="imsg-error">
                  {message.error}
                  {onRetry && !message.template_name ? (
                    <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => onRetry(message)}>
                      <RotateCcw size={13} /> Retry
                    </button>
                  ) : null}
                </div>
              ) : null}
            </div>
          </Fragment>
        );
      })}
    </div>
  );
}

/** Keeps a scrolling container pinned to the newest message. */
export function useStickToBottom(dependency: unknown) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = ref.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [dependency]);
  return ref;
}

function renderTemplate(text: string, values: Record<string, string>) {
  return text.replace(/\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g, (match, name: string) => values[name] || match);
}

/**
 * Write and send an SMS or WhatsApp message to one number. WhatsApp outside the
 * customer's 24-hour window only allows approved templates, so the composer
 * switches to templates there.
 */
export function Composer({
  number,
  channels,
  whatsappWindowUntil,
  optedOut,
  record,
  onSent,
  compact,
}: {
  number: string;
  channels: ChannelOption[];
  whatsappWindowUntil: string | null;
  optedOut: OptOut;
  record?: { type: string; id: string } | null;
  onSent: (message: ChannelMessage) => void;
  compact?: boolean;
}) {
  const toast = useToast();
  const ready = channels.filter((c) => c.ready);
  const windowOpen = Boolean(whatsappWindowUntil && new Date(whatsappWindowUntil).getTime() > Date.now());
  const preferred: ChannelKey | null = ready.find((c) => c.channel === "whatsapp") && windowOpen ? "whatsapp" : (ready.find((c) => c.channel === "sms")?.channel ?? ready[0]?.channel ?? null);
  const [channel, setChannel] = useState<ChannelKey | null>(preferred);
  const [text, setText] = useState("");
  const [templateKey, setTemplateKey] = useState("");
  const [values, setValues] = useState<{ body: Record<string, string>; header: Record<string, string>; media: string }>({ body: {}, header: {}, media: "" });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (channel && !ready.some((c) => c.channel === channel)) setChannel(preferred);
    if (!channel && preferred) setChannel(preferred);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready.map((c) => c.channel).join(","), preferred]);

  const option = ready.find((c) => c.channel === channel) ?? null;
  const templates = option?.templates ?? [];
  const mustTemplate = channel === "whatsapp" && !windowOpen;
  const [useTemplate, setUseTemplate] = useState(false);
  const templateMode = channel === "whatsapp" && (mustTemplate || useTemplate);
  const template = templates.find((t) => `${t.name}|${t.language}` === templateKey) ?? null;
  const segments = useMemo(() => (channel === "sms" ? smsSegments(text) : null), [channel, text]);

  if (!ready.length) {
    return (
      <p className="ui-muted ui-small imsg-composer-note">
        Turn on and connect Twilio SMS or WhatsApp in <a href="/integrations">Integrations</a> to send messages.
      </p>
    );
  }
  if (optedOut) {
    return (
      <div className="imsg-composer-note">
        <Alert tone="warning">
          {prettyNumber(number)} {optedOut.reason === "unsubscribed" ? "replied STOP" : "is on the do-not-contact list"}, so AIVA won&apos;t message it.
          {optedOut.reason === "unsubscribed" ? " They can text START to opt back in." : ""}
        </Alert>
      </div>
    );
  }

  async function send() {
    if (!channel || busy) return;
    const body = text.trim();
    if (!templateMode && !body) return;
    setBusy(true);
    try {
      const payload: Record<string, unknown> = { channel, to: number, ...(record ? { record_type: record.type, record_id: record.id } : {}) };
      if (templateMode) {
        if (!template) {
          toast("Choose a template.", "danger");
          return;
        }
        payload.template = {
          name: template.name,
          language: template.language,
          params: { body: values.body, header: values.header, ...(values.media ? { header_media_url: values.media } : {}) },
        };
      } else {
        payload.body = body;
      }
      const result = await api.post<SendResult>("/messaging/send", payload);
      onSent(result.message);
      if (!result.ok) {
        toast(result.error ?? "The message wasn't sent.", "danger");
      } else {
        setText("");
        setValues({ body: {}, header: {}, media: "" });
      }
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      void send();
    }
  }

  const mediaHeader = template?.header?.format && ["IMAGE", "VIDEO", "DOCUMENT"].includes(template.header.format) ? template.header.format.toLowerCase() : null;

  return (
    <div className={`imsg-composer${compact ? " compact" : ""}`}>
      <div className="imsg-composer-row">
        <select className="ui-select sm" aria-label="Send with" value={channel ?? ""} onChange={(event) => setChannel(event.target.value as ChannelKey)} style={{ width: "auto" }}>
          {ready.map((c) => (
            <option key={c.channel} value={c.channel}>
              {c.label}
              {c.from ? ` · from ${prettyNumber(c.from)}` : ""}
            </option>
          ))}
        </select>
        {channel === "whatsapp" && !mustTemplate ? (
          <label className="ui-check ui-small">
            <input type="checkbox" checked={useTemplate} onChange={(event) => setUseTemplate(event.target.checked)} /> Use a template
          </label>
        ) : null}
        <span className="ui-spacer" />
        {channel === "whatsapp" ? (
          <span className="ui-muted ui-small" title={whatsappWindowUntil ? `Until ${dateTime(whatsappWindowUntil)}` : undefined}>
            {windowOpen ? `Reply window open · closes ${relativeTime(whatsappWindowUntil)}` : "Outside the 24-hour window · templates only"}
          </span>
        ) : segments && segments.segments > 1 ? (
          <span className="ui-muted ui-small">
            {segments.segments} texts{segments.unicode ? " (special characters)" : ""}
          </span>
        ) : null}
      </div>

      {templateMode ? (
        templates.length ? (
          <div className="imsg-template-form">
            <Field label="Template">
              <select className="ui-select sm" value={templateKey} onChange={(event) => { setTemplateKey(event.target.value); setValues({ body: {}, header: {}, media: "" }); }}>
                <option value="">Choose an approved template…</option>
                {templates.map((t) => (
                  <option key={`${t.name}|${t.language}`} value={`${t.name}|${t.language}`}>
                    {t.name} · {t.language}
                    {t.category ? ` · ${t.category.toLowerCase()}` : ""}
                  </option>
                ))}
              </select>
            </Field>
            {template ? (
              <>
                {mediaHeader ? (
                  <Field label={`Header ${mediaHeader} link`} hint="A public https:// link to the file.">
                    <input className="ui-input sm" value={values.media} onChange={(event) => setValues((v) => ({ ...v, media: event.target.value }))} placeholder="https://…" />
                  </Field>
                ) : null}
                {(template.header?.params ?? []).map((name) => (
                  <Field key={`h-${name}`} label={`Header {{${name}}}`}>
                    <input className="ui-input sm" value={values.header[name] ?? ""} onChange={(event) => setValues((v) => ({ ...v, header: { ...v.header, [name]: event.target.value } }))} />
                  </Field>
                ))}
                {template.body_params.map((name) => (
                  <Field key={`b-${name}`} label={`{{${name}}}`}>
                    <input className="ui-input sm" value={values.body[name] ?? ""} onChange={(event) => setValues((v) => ({ ...v, body: { ...v.body, [name]: event.target.value } }))} />
                  </Field>
                ))}
                <div className="imsg-template-preview">
                  {template.header?.text ? <strong>{renderTemplate(template.header.text, values.header)}</strong> : null}
                  <div>{renderTemplate(template.body, values.body)}</div>
                  {template.footer ? <div className="ui-muted ui-small">{template.footer}</div> : null}
                </div>
              </>
            ) : null}
          </div>
        ) : (
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            There are no approved WhatsApp templates yet. Create one in WhatsApp Manager, then sync templates on the WhatsApp integration page.
          </p>
        )
      ) : (
        <textarea
          className="ui-textarea"
          rows={compact ? 2 : 3}
          value={text}
          maxLength={channel === "whatsapp" ? 4096 : 1600}
          placeholder={`Write a ${channel === "whatsapp" ? "WhatsApp message" : "text"}… (Ctrl+Enter to send)`}
          aria-label="Message"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKey}
        />
      )}
      <div className="imsg-composer-row">
        <span className="ui-muted ui-small">To {prettyNumber(number)}</span>
        <span className="ui-spacer" />
        <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" disabled={busy || (templateMode ? !template : !text.trim())} onClick={() => void send()}>
          {busy ? <span className="ui-spinner" /> : <Send size={14} />} Send
        </button>
      </div>
    </div>
  );
}
