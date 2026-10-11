// Auth routes — /api/auth/login, /api/auth/logout, /api/auth/me (ported from backend/routers/auth.py).
import type { Config } from "@netlify/functions";
import { eq } from "drizzle-orm";
import { db } from "../../db/index.js";
import { users } from "../../db/schema.js";
import {
  clearedSessionCookie,
  createToken,
  errorResponse,
  getCurrentUser,
  handle,
  isEmail,
  sessionCookie,
  userOut,
  verifyPassword,
} from "../lib/auth.js";

export default (req: Request) =>
  handle(async () => {
    const action = new URL(req.url).pathname.split("/").pop();

    if (action === "login" && req.method === "POST") {
      const body = (await req.json().catch(() => null)) as { email?: unknown; password?: unknown } | null;
      if (!body || !isEmail(body.email) || typeof body.password !== "string") {
        return errorResponse(422, [{ loc: ["body"], msg: "Email dan password wajib diisi", type: "value_error" }]);
      }
      const [user] = await db.select().from(users).where(eq(users.email, body.email.toLowerCase().trim()));
      if (!user || !verifyPassword(body.password, user.passwordHash)) {
        return errorResponse(401, "Email atau password salah");
      }
      const now = new Date();
      await db.update(users).set({ lastLoginAt: now }).where(eq(users.id, user.id));
      return Response.json(userOut(user), {
        headers: { "Set-Cookie": sessionCookie(await createToken(user.id)) },
      });
    }

    if (action === "logout" && req.method === "POST") {
      return Response.json({ ok: true }, { headers: { "Set-Cookie": clearedSessionCookie() } });
    }

    if (action === "me" && req.method === "GET") {
      return Response.json(userOut(await getCurrentUser(req)));
    }

    return errorResponse(405, "Method Not Allowed");
  });

export const config: Config = {
  path: ["/api/auth/login", "/api/auth/logout", "/api/auth/me"],
};
