"use client";

import QRCode from "qrcode";
import { Download, Smartphone } from "lucide-react";
import { useEffect, useState } from "react";

import { SettingsShell } from "@/components/settings-shell";
import { Alert, Card, PageHeader } from "@/components/ui";

interface InstallPrompt extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

export default function MobilePage() {
  return (
    <SettingsShell feature="mobile_app">
      <MobileApp />
    </SettingsShell>
  );
}

function MobileApp() {
  const [qr, setQr] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [prompt, setPrompt] = useState<InstallPrompt | null>(null);
  const [installed, setInstalled] = useState(false);

  useEffect(() => {
    const origin = window.location.origin;
    setUrl(origin);
    void QRCode.toDataURL(`${origin}/login`, { margin: 1, width: 220 }).then(setQr);
    setInstalled(window.matchMedia("(display-mode: standalone)").matches);
    const onPrompt = (event: Event) => {
      event.preventDefault();
      setPrompt(event as InstallPrompt);
    };
    const onInstalled = () => setInstalled(true);
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  async function install() {
    if (!prompt) return;
    await prompt.prompt();
    const choice = await prompt.userChoice;
    if (choice.outcome === "accepted") setInstalled(true);
    setPrompt(null);
  }

  return (
    <>
      <PageHeader
        title="Mobile app"
        description="AIVA installs on iPhone, Android, Mac and Windows straight from the browser — no app store needed. It opens full screen, gets its own icon, and works with calls, email and maps on your phone."
      />
      <div className="ui-split" style={{ alignItems: "start" }}>
        <Card title="Scan to open AIVA on your phone" icon={<Smartphone size={16} />}>
          <div className="mobile-qr">
            {qr ? <img src={qr} alt={`QR code for ${url}`} width={220} height={220} /> : <div className="ui-skeleton" style={{ width: 220, height: 220 }} />}
            <span className="ui-mono ui-small">{url}</span>
          </div>
          {installed ? (
            <Alert tone="success">AIVA is installed on this device.</Alert>
          ) : prompt ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => void install()}>
              <Download size={15} /> Install on this device
            </button>
          ) : null}
        </Card>
        <div className="ui-stack">
          <Card title="iPhone and iPad">
            <ol className="install-steps">
              <li>Open the link in Safari and sign in.</li>
              <li>
                Tap the <strong>Share</strong> button.
              </li>
              <li>
                Choose <strong>Add to Home Screen</strong>, then <strong>Add</strong>.
              </li>
            </ol>
          </Card>
          <Card title="Android">
            <ol className="install-steps">
              <li>Open the link in Chrome and sign in.</li>
              <li>
                Tap <strong>Install app</strong> when it&apos;s offered, or open the <strong>⋮</strong> menu and choose <strong>Install app</strong>.
              </li>
            </ol>
          </Card>
          <Card title="Desktop">
            <ol className="install-steps">
              <li>
                In Chrome or Edge, click the install icon at the right of the address bar — or use the button on this page.
              </li>
            </ol>
          </Card>
        </div>
      </div>
    </>
  );
}
