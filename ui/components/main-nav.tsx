"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { cn } from "@/lib/utils"

const LINKS = [
  { href: "/", label: "Runs" },
  { href: "/new", label: "New run" },
  { href: "/agents", label: "Agents" },
] as const

export function MainNav() {
  const pathname = usePathname()
  return (
    <nav aria-label="Main" className="flex gap-1 text-sm">
      {LINKS.map(({ href, label }) => {
        const current = pathname === href
        return (
          <Link
            key={href}
            href={href}
            aria-current={current ? "page" : undefined}
            className={cn(
              "rounded-md px-2.5 py-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
              current && "bg-accent font-medium text-foreground",
            )}
          >
            {label}
          </Link>
        )
      })}
    </nav>
  )
}
