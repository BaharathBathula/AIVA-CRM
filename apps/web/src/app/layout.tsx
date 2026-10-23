import type { Metadata, Viewport } from "next";

import "@fontsource-variable/manrope";

import { Providers } from "@/components/providers";

import "./globals.css";
import "./theme.css";
import "./platform.css";
import "./editions.css";

export const metadata: Metadata = {
  title: {
    default: "AIVA CRM",
    template: "%s · AIVA CRM",
  },
  description: "AIVA CRM: sales, service, analytics and automation in one workspace.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ecebef" },
    { media: "(prefers-color-scheme: dark)", color: "#111014" },
  ],
};

// Runs before first paint: apply the saved theme (or the system preference)
// so the page never flashes the wrong colours.
const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem("aiva-theme");if(t!=="light"&&t!=="dark"){t=window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"}document.documentElement.dataset.theme=t}catch(e){document.documentElement.dataset.theme="light"}})();`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
