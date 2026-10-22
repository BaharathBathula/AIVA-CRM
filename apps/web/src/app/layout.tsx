import type { Metadata, Viewport } from "next";

import { Providers } from "@/components/providers";

import "./globals.css";
import "./platform.css";

export const metadata: Metadata = {
  title: {
    default: "AIVA CRM",
    template: "%s · AIVA CRM",
  },
  description: "AIVA CRM — sales, service, analytics and automation in one workspace.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#10131a",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
