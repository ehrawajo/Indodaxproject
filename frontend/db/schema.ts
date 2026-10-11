import { pgTable, text, timestamp } from "drizzle-orm/pg-core";

// Mirrors the Mongo `users` collection from the FastAPI backend (same id/hash formats).
export const users = pgTable("users", {
  id: text().primaryKey(),
  email: text().notNull().unique(),
  name: text().notNull().default(""),
  role: text().notNull().default("user"),
  passwordHash: text("password_hash").notNull(),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  lastLoginAt: timestamp("last_login_at", { withTimezone: true }),
});

// Server-side key/value secrets (e.g. the session signing key), never exposed over HTTP.
export const appSecrets = pgTable("app_secrets", {
  key: text().primaryKey(),
  value: text().notNull(),
});
