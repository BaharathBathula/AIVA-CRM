import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "AIVA CRM",
    short_name: "AIVA",
    description: "Sales, service, marketing and AI in one workspace.",
    id: "/",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "any",
    background_color: "#ecebef",
    theme_color: "#ecebef",
    categories: ["business", "productivity"],
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
    shortcuts: [
      { name: "New lead", url: "/leads?new=1", icons: [{ src: "/icon-192.png", sizes: "192x192" }] },
      { name: "Tasks", url: "/tasks", icons: [{ src: "/icon-192.png", sizes: "192x192" }] },
      { name: "Calls", url: "/calls", icons: [{ src: "/icon-192.png", sizes: "192x192" }] },
    ],
  };
}
