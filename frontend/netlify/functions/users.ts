// Admin-only user management: list, invite (create with initial password), delete, reset password.
// Ported from backend/routers/users.py.
import type { Config, Context } from "@netlify/functions";
import { asc, eq } from "drizzle-orm";
import { randomUUID } from "node:crypto";
import { db } from "../../db/index.js";
import { users } from "../../db/schema.js";
import {
  errorResponse,
  generateInitialPassword,
  handle,
  hashPassword,
  isEmail,
  requireAdmin,
  userOut,
} from "../lib/auth.js";

export default (req: Request, context: Context) =>
  handle(async () => {
    const admin = await requireAdmin(req);
    const { id } = context.params;
    const isPasswordRoute = new URL(req.url).pathname.endsWith("/password");

    if (!id && req.method === "GET") {
      const rows = await db.select().from(users).orderBy(asc(users.createdAt)).limit(500);
      return Response.json(rows.map(userOut));
    }

    if (!id && req.method === "POST") {
      const body = (await req.json().catch(() => null)) as { email?: unknown; name?: unknown; role?: unknown } | null;
      const role = body?.role ?? "user";
      if (!body || !isEmail(body.email)) {
        return errorResponse(422, [{ loc: ["body", "email"], msg: "Email tidak valid", type: "value_error" }]);
      }
      if (role !== "user" && role !== "admin") {
        return errorResponse(422, [{ loc: ["body", "role"], msg: "role harus 'user' atau 'admin'", type: "value_error" }]);
      }
      const email = body.email.toLowerCase().trim();
      const [existing] = await db.select({ id: users.id }).from(users).where(eq(users.email, email));
      if (existing) return errorResponse(409, "Email sudah terdaftar");
      const initial = generateInitialPassword();
      const [created] = await db
        .insert(users)
        .values({
          id: randomUUID(),
          email,
          name: typeof body.name === "string" ? body.name.trim() : "",
          role,
          passwordHash: hashPassword(initial),
        })
        .returning();
      return Response.json({ user: userOut(created), initial_password: initial }, { status: 201 });
    }

    if (!id) return errorResponse(405, "Method Not Allowed");

    const [doc] = await db.select().from(users).where(eq(users.id, id));
    if (!doc) return errorResponse(404, "Pengguna tidak ditemukan");

    if (isPasswordRoute && req.method === "PATCH") {
      const newPassword = generateInitialPassword();
      await db.update(users).set({ passwordHash: hashPassword(newPassword) }).where(eq(users.id, id));
      return Response.json({ user: userOut(doc), new_password: newPassword });
    }

    if (!isPasswordRoute && req.method === "DELETE") {
      if (doc.id === admin.id) return errorResponse(400, "Tidak dapat menghapus akun sendiri");
      if (doc.role === "admin") {
        const admins = await db.select({ id: users.id }).from(users).where(eq(users.role, "admin"));
        if (admins.length <= 1) return errorResponse(400, "Harus tersisa minimal satu admin");
      }
      await db.delete(users).where(eq(users.id, id));
      return Response.json({ ok: true });
    }

    return errorResponse(405, "Method Not Allowed");
  });

export const config: Config = {
  path: ["/api/users", "/api/users/:id", "/api/users/:id/password"],
};
