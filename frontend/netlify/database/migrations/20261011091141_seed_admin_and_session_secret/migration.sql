-- Built-in admin account (same as the FastAPI backend bootstrap). Only a PBKDF2 hash is stored.
INSERT INTO "users" ("id", "email", "name", "role", "password_hash")
VALUES (gen_random_uuid()::text, 'chellypartang@gmail.com', 'Administrator', 'admin', 'pbkdf2_sha256$200000$e1e9483fc6e5a988a7e0813ff18cc09b$3dfad086ae9ae8a6438c06127eb37c44e8bbdbc9ad2999ed719422bee84825b9')
ON CONFLICT ("email") DO NOTHING;
--> statement-breakpoint
-- Random per-database key for signing session cookies (used when SESSION_SECRET is not set).
INSERT INTO "app_secrets" ("key", "value")
VALUES ('session_secret', replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''))
ON CONFLICT ("key") DO NOTHING;
