// Auth primitives ported from backend/lib/auth.py: PBKDF2 password hashing, HS256 JWT
// session cookies (httpOnly, never tokens in JSON). Hash and token formats match the Python side.
import { createHmac, pbkdf2Sync, randomBytes, timingSafeEqual } from "node:crypto";
import { eq } from "drizzle-orm";
import { db } from "../../db/index.js";
import { appSecrets, users } from "../../db/schema.js";

export const COOKIE_NAME = "icl_session";
const TOKEN_TTL_DAYS = 7;

export type User = typeof users.$inferSelect;

export class HttpError extends Error {
  status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

// FastAPI-compatible error body: the frontend reads `body.detail`.
export const errorResponse = (status: number, detail: unknown) => Response.json({ detail }, { status });

export async function handle(fn: () => Promise<Response>): Promise<Response> {
  try {
    return await fn();
  } catch (err) {
    if (err instanceof HttpError) return errorResponse(err.status, err.message);
    console.error(err);
    return errorResponse(500, "Terjadi kesalahan pada server");
  }
}

// --- passwords (PBKDF2-SHA256) ---

export function hashPassword(password: string): string {
  const salt = randomBytes(16).toString("hex");
  const iterations = 200_000;
  const dk = pbkdf2Sync(password, salt, iterations, 32, "sha256").toString("hex");
  return `pbkdf2_sha256$${iterations}$${salt}$${dk}`;
}

export function verifyPassword(password: string, stored: string | null | undefined): boolean {
  if (!stored) return false;
  const [scheme, iterations, salt, hexhash] = stored.split("$");
  if (scheme !== "pbkdf2_sha256" || !salt || !hexhash) return false;
  const dk = pbkdf2Sync(password, salt, Number(iterations), 32, "sha256");
  const expected = Buffer.from(hexhash, "hex");
  return expected.length === dk.length && timingSafeEqual(dk, expected);
}

export const generateInitialPassword = () => randomBytes(9).toString("base64url");

// --- JWT session tokens (HS256) ---

let cachedSecret: string | undefined;

async function getSecret(): Promise<string> {
  if (process.env.SESSION_SECRET) return process.env.SESSION_SECRET;
  if (cachedSecret) return cachedSecret;
  const [row] = await db.select().from(appSecrets).where(eq(appSecrets.key, "session_secret"));
  if (!row) throw new Error("session_secret missing from app_secrets");
  cachedSecret = row.value;
  return cachedSecret;
}

const b64url = (input: Buffer | string) => Buffer.from(input).toString("base64url");

async function sign(data: string): Promise<string> {
  return createHmac("sha256", await getSecret()).update(data).digest("base64url");
}

export async function createToken(userId: string): Promise<string> {
  const header = b64url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const exp = Math.floor(Date.now() / 1000) + TOKEN_TTL_DAYS * 24 * 3600;
  const payload = b64url(JSON.stringify({ sub: userId, exp }));
  return `${header}.${payload}.${await sign(`${header}.${payload}`)}`;
}

async function verifyToken(token: string): Promise<string | null> {
  const [header, payload, signature] = token.split(".");
  if (!header || !payload || !signature) return null;
  const expected = Buffer.from(await sign(`${header}.${payload}`));
  const actual = Buffer.from(signature);
  if (expected.length !== actual.length || !timingSafeEqual(expected, actual)) return null;
  try {
    const claims = JSON.parse(Buffer.from(payload, "base64url").toString()) as { sub?: string; exp?: number };
    if (!claims.sub || !claims.exp || claims.exp * 1000 < Date.now()) return null;
    return claims.sub;
  } catch {
    return null;
  }
}

export function sessionCookie(token: string): string {
  const secure = process.env.NETLIFY_DEV === "true" ? "" : "; Secure";
  return `${COOKIE_NAME}=${token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=${TOKEN_TTL_DAYS * 24 * 3600}${secure}`;
}

export const clearedSessionCookie = () => `${COOKIE_NAME}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0`;

function readCookie(req: Request, name: string): string | undefined {
  for (const part of (req.headers.get("cookie") ?? "").split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return v.join("=");
  }
  return undefined;
}

// --- request guards ---

export async function getCurrentUser(req: Request): Promise<User> {
  const token = readCookie(req, COOKIE_NAME);
  if (!token) throw new HttpError(401, "Not authenticated");
  const userId = await verifyToken(token);
  if (!userId) throw new HttpError(401, "Sesi tidak valid atau sudah kedaluwarsa");
  const [user] = await db.select().from(users).where(eq(users.id, userId));
  if (!user) throw new HttpError(401, "Akun tidak ditemukan");
  return user;
}

export async function requireAdmin(req: Request): Promise<User> {
  const user = await getCurrentUser(req);
  if (user.role !== "admin") throw new HttpError(403, "Akses khusus admin");
  return user;
}

// Matches the Pydantic UserOut model (never includes the password hash).
export const userOut = (u: User) => ({
  id: u.id,
  email: u.email,
  name: u.name,
  role: u.role,
  created_at: u.createdAt?.toISOString() ?? null,
  last_login_at: u.lastLoginAt?.toISOString() ?? null,
});

export const isEmail = (v: unknown): v is string => typeof v === "string" && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim());
