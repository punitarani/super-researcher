import type { Metadata } from "next"
import { NuqsAdapter } from "nuqs/adapters/next/app"
import { SiteHeader } from "@/components/site-header"
import "./globals.css"

export const metadata: Metadata = {
  title: "SuperResearcher",
  description: "Run and manage research agents locally.",
}

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body className="min-h-dvh antialiased">
        <NuqsAdapter>
          <SiteHeader />
          <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
        </NuqsAdapter>
      </body>
    </html>
  )
}
