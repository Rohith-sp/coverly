import type { Metadata } from "next";
import { Suspense } from "react";
import Link from "next/link";
import { Loading } from "@/components/Loading";
import "./globals.css";

export const metadata: Metadata = {
  title: "Coverly",
  description: "The smallest set of YouTube videos that covers your whole study plan.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">
        <header className="border-b border-zinc-200 dark:border-zinc-800">
          <div className="mx-auto max-w-3xl px-4 py-3">
            <Link href="/" className="text-lg font-semibold focus-visible:outline-2 focus-visible:outline-indigo-500">
              Coverly
            </Link>
          </div>
        </header>
        <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-8"><Suspense fallback={<Loading />}>{children}</Suspense></main>
      </body>
    </html>
  );
}
