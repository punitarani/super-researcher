import { NextResponse, type NextRequest } from "next/server"

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"])

/** True when a request is addressed to this computer. */
export function isLocalHost(host: string | null): boolean {
  return !!host && LOCAL_HOSTS.has(host.replace(/:\d+$/, ""))
}

// Refuse requests addressed to any other name, so a web page can't reach the app
// by pointing its own domain at 127.0.0.1 (DNS rebinding).
export function proxy(request: NextRequest) {
  if (!isLocalHost(request.headers.get("host"))) {
    return new NextResponse("SuperResearcher only answers requests addressed to localhost.", { status: 403 })
  }
  return NextResponse.next()
}
