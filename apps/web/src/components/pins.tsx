"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { MessagesSquare, Pin, PinOff, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useSession, useToast } from "@/components/providers";
import { PINS_CHANGED, type PinItem } from "@/lib/workspace";
import { api, errorMessage } from "@/lib/use-api";

function announce() {
  window.dispatchEvent(new Event(PINS_CHANGED));
}

function useDismiss(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    function onDown(event: MouseEvent) {
      if (!ref.current?.contains(event.target as Node)) close();
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") close();
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);
  return ref;
}

/** "My Pins" in the top bar: records and pages you pinned for quick access. */
export function PinsMenu() {
  const { session } = useSession();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pins, setPins] = useState<PinItem[]>([]);
  const close = useCallback(() => setOpen(false), []);
  const ref = useDismiss(open, close);

  const load = useCallback(async () => {
    try {
      setPins((await api.get<{ items: PinItem[] }>("/pins")).items);
    } catch {
      // Best effort.
    }
  }, []);

  useEffect(() => {
    if (!session) return;
    void load();
    window.addEventListener(PINS_CHANGED, load);
    return () => window.removeEventListener(PINS_CHANGED, load);
  }, [load, session]);

  async function unpin(pin: PinItem) {
    setPins((current) => current.filter((p) => p.id !== pin.id));
    await api.del(`/pins/${pin.id}`).catch(() => undefined);
    announce();
  }

  async function move(index: number, delta: number) {
    const next = [...pins];
    const [item] = next.splice(index, 1);
    next.splice(index + delta, 0, item);
    setPins(next);
    await api.put("/pins/order", { ids: next.map((p) => p.id) }).catch(() => undefined);
  }

  return (
    <div className="shell-pop" ref={ref}>
      <button
        type="button"
        className="shell-icon"
        aria-label={`My pins${pins.length ? `, ${pins.length}` : ""}`}
        title="My pins"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <Pin size={19} strokeWidth={1.8} />
      </button>
      {open ? (
        <div className="ui-popover shell-menu pins-menu" role="dialog" aria-label="My pins">
          <div className="ui-row" style={{ padding: "8px 10px 6px" }}>
            <strong style={{ fontSize: 15 }}>My pins</strong>
            <span className="ui-spacer" />
            <span className="ui-muted ui-small">{pins.length} pinned</span>
          </div>
          {pins.length ? (
            <ul className="pins-list">
              {pins.map((pin, index) => (
                <li key={pin.id}>
                  <button
                    type="button"
                    className="pins-open"
                    onClick={() => {
                      setOpen(false);
                      router.push(pin.href);
                    }}
                  >
                    <span className="pins-label">{pin.label}</span>
                    <span className="pins-sub">{pin.subtitle}</span>
                  </button>
                  <span className="pins-tools">
                    <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Move ${pin.label} up`} disabled={index === 0} onClick={() => void move(index, -1)}>
                      ↑
                    </button>
                    <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Unpin ${pin.label}`} onClick={() => void unpin(pin)}>
                      <X size={14} />
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="ui-muted ui-small" style={{ padding: "4px 12px 14px", margin: 0 }}>
              Pin accounts, deals, orders, projects or any page with the <Pin size={12} style={{ verticalAlign: -1 }} /> button to keep them one click away.
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}

/** Pin or unpin a record (or a page) for the signed-in user. */
export function PinButton({ type, id, label, href }: { type: string; id: string; label?: string; href?: string }) {
  const toast = useToast();
  const [pin, setPin] = useState<PinItem | null | undefined>(undefined);

  const check = useCallback(async () => {
    try {
      const items = (await api.get<{ items: PinItem[] }>("/pins")).items;
      setPin(items.find((p) => p.record_type === type && p.record_id === id) ?? null);
    } catch {
      setPin(null);
    }
  }, [type, id]);

  useEffect(() => {
    void check();
    window.addEventListener(PINS_CHANGED, check);
    return () => window.removeEventListener(PINS_CHANGED, check);
  }, [check]);

  async function toggle() {
    try {
      if (pin) {
        await api.del(`/pins/${pin.id}`);
        setPin(null);
        toast("Unpinned.");
      } else {
        setPin(await api.post<PinItem>("/pins", { record_type: type, record_id: id, label, href }));
        toast("Pinned. Find it under My pins in the top bar.");
      }
      announce();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  return (
    <button
      type="button"
      className="ui-btn no-print"
      onClick={() => void toggle()}
      disabled={pin === undefined}
      aria-pressed={Boolean(pin)}
      title={pin ? "Remove from My pins" : "Add to My pins"}
    >
      {pin ? <PinOff size={15} /> : <Pin size={15} />} {pin ? "Pinned" : "Pin"}
    </button>
  );
}

/** Team chat shortcut with the number of unread messages. */
export function ChatButton() {
  const { session, has } = useSession();
  const [unread, setUnread] = useState(0);
  const enabled = Boolean(session) && has("team_chat");

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    async function load() {
      try {
        const result = await api.get<{ unread: number }>("/chat/unread");
        if (active) setUnread(result.unread);
      } catch {
        // Best effort.
      }
    }
    void load();
    const timer = window.setInterval(() => void load(), 30000);
    window.addEventListener("aiva:chat-read", load);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.removeEventListener("aiva:chat-read", load);
    };
  }, [enabled]);

  if (!enabled) return null;
  return (
    <Link href="/chat" className="shell-icon" aria-label={unread ? `Chat, ${unread} unread` : "Chat"} title="Chat">
      <MessagesSquare size={19} strokeWidth={1.8} />
      {unread ? <span className="shell-count">{unread > 99 ? "99+" : unread}</span> : null}
    </Link>
  );
}
