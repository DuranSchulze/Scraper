import { NextResponse, type NextRequest } from "next/server";

// Cheap gate only: a missing cookie goes to /login. The API verifies the token on every call.
export function proxy(request: NextRequest) {
  if (request.cookies.has("scraper_session")) return NextResponse.next();
  const url = new URL("/login", request.url);
  const next = request.nextUrl.pathname + request.nextUrl.search;
  if (next !== "/") url.searchParams.set("next", next);
  return NextResponse.redirect(url);
}

export const config = { matcher: ["/((?!api|_next|login|favicon.ico).*)"] };
