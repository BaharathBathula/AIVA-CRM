import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AIVA CRM",
  description:
    "AI-native autonomous customer relationship management platform",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
