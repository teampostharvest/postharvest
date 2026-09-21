import type { Metadata, Viewport } from "next";
import "./globals.css";
import "motion-icons-react/style.css";
import { generalSans, jetbrainsMono, satoshi } from "./fonts";
import { ThemeProvider } from "@/components/common/ThemeProvider";
import { AuthProvider } from "@/lib/auth-context";

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: "PostHarvest",
    template: "%s · PostHarvest",
  },
  description:
    "Extract publicly available Facebook page and profile posts with live progress tracking, preview, and JSON / CSV / Excel export. Throttled and compliant, no auth bypass.",
  alternates: {
    canonical: "/",
  },
  openGraph: {
    type: "website",
    url: SITE_URL,
    siteName: "PostHarvest",
    title: "PostHarvest",
    description:
      "Extract publicly available Facebook page and profile posts with live progress tracking, preview, and JSON / CSV / Excel export. Throttled and compliant, no auth bypass.",
    locale: "en_US",
  },
  twitter: {
    card: "summary_large_image",
    title: "PostHarvest",
    description:
      "Extract publicly available Facebook posts with live progress tracking and JSON / CSV / Excel export.",
  },
  robots: {
    index: true,
    follow: true,
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f7f5f0" },
    { media: "(prefers-color-scheme: dark)", color: "#121110" },
  ],
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`light ${generalSans.variable} ${satoshi.variable} ${jetbrainsMono.variable}`}
      suppressHydrationWarning
    >
      <body className="min-h-screen bg-background font-sans text-foreground antialiased">
        <ThemeProvider>
          <AuthProvider>{children}</AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}