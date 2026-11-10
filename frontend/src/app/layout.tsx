import type { Metadata } from "next";
import { Manrope, Geist_Mono } from "next/font/google";
import { Providers } from "@/components/providers";
import "./globals.css";

const manrope = Manrope({ variable: "--font-sans", subsets: ["latin"], weight: ["400", "500", "600", "700"] });
const mono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Workspace Scraper",
  description: "Review scraped client records before publishing them to the official table.",
};

// Runs before first paint so a saved collapsed sidebar never flashes open (see globals.css, app-shell.tsx).
const SIDEBAR_INIT = `try{document.documentElement.dataset.sidebar=localStorage.getItem("scraper.sidebar.collapsed")==="1"?"collapsed":"expanded"}catch(e){}`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${manrope.variable} ${mono.variable} h-full antialiased`} suppressHydrationWarning>
      <head><script dangerouslySetInnerHTML={{ __html: SIDEBAR_INIT }} /></head>
      <body className="min-h-full">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
