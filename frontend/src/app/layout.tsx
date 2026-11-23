import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { Suspense } from "react";

import { AppNavigation, CurrentAppNavigation } from "@/components/app-navigation";

import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Chess Tutor",
  description: "Play at your level with an AI coach that notices what you miss.",
};

export const viewport: Viewport = { themeColor: "#5b3fd6" };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col bg-background text-foreground md:flex-row">
        <Suspense fallback={<AppNavigation currentPath="" />}>
          <CurrentAppNavigation />
        </Suspense>
        <main className="flex flex-1 flex-col px-4 pt-6 pb-24 md:px-8 md:pb-6">{children}</main>
      </body>
    </html>
  );
}
