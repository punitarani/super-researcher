import { describe, expect, it } from "vitest"
import { isLocalHost } from "@/proxy"

describe("isLocalHost", () => {
  it("accepts this computer and nothing else", () => {
    for (const host of ["localhost:3000", "127.0.0.1:3000", "[::1]:3000", "localhost"]) expect(isLocalHost(host)).toBe(true)
    for (const host of ["evil.example:3000", "127.0.0.1.evil.example", "", null]) expect(isLocalHost(host)).toBe(false)
  })
})
