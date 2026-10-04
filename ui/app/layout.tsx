import type { Metadata } from "next"
import { NuqsAdapter } from "nuqs/adapters/next/app"
import "./globals.css"

export const metadata: Metadata = {
  title: "SuperResearcher",
  description: "Run and manage research agents locally.",
}

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body className="min-h-dvh antialiased">
        <NuqsAdapter>{children}</NuqsAdapter>
      </body>
    </html>
  )
}
