const COOKIE = "fomo_desk";

export interface Env {
  DESK_SECRET?: string;
}

function hex(bytes: ArrayBuffer): string {
  return [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function hmacHex(secret: string, msg: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(msg));
  return hex(sig);
}

async function cookieOk(value: string | undefined, secret: string): Promise<boolean> {
  if (!value || !value.includes(".")) return false;
  const dot = value.indexOf(".");
  const expS = value.slice(0, dot);
  const sig = value.slice(dot + 1);
  const exp = Number(expS);
  if (!Number.isFinite(exp) || exp < Date.now() / 1000) return false;
  const expected = await hmacHex(secret, expS);
  if (expected.length !== sig.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i += 1) {
    diff |= expected.charCodeAt(i) ^ sig.charCodeAt(i);
  }
  return diff === 0;
}

function cookieValue(request: Request): string | undefined {
  const raw = request.headers.get("Cookie") || "";
  for (const part of raw.split(";")) {
    const [name, ...rest] = part.trim().split("=");
    if (name === COOKIE) return rest.join("=");
  }
  return undefined;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const secret = env.DESK_SECRET?.trim();
    if (!secret) {
      return fetch(request);
    }
    const url = new URL(request.url);
    const open =
      request.method === "OPTIONS" ||
      (url.pathname === "/api/unlock" && (request.method === "GET" || request.method === "POST"));
    if (open || (await cookieOk(cookieValue(request), secret))) {
      return fetch(request);
    }
    if (url.pathname.startsWith("/api/")) {
      return Response.json({ ok: false, locked: true, message: "PIN required" }, { status: 401 });
    }
    return fetch(request);
  },
};
