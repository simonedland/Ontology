-- tgstore schema. Idempotent: applied on every API start. No foreign keys on purpose: records arrive
-- in any order (a message before its sender), from many sources, and must never be rejected for it.

CREATE TABLE IF NOT EXISTS chats (
    id            bigint PRIMARY KEY,
    type          text,
    title         text,
    username      text,
    about         text,
    member_count  integer,
    extra         jsonb NOT NULL DEFAULT '{}',
    first_seen    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id          bigint PRIMARY KEY,
    username    text,
    first_name  text,
    last_name   text,
    phone       text,
    bio         text,
    lang_code   text,
    is_bot      boolean,
    extra       jsonb NOT NULL DEFAULT '{}',
    first_seen  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS users_username ON users (lower(username));

-- Profile values as they were before a change, and when they were replaced.
CREATE TABLE IF NOT EXISTS user_history (
    user_id      bigint NOT NULL,
    replaced_at  timestamptz NOT NULL DEFAULT now(),
    username     text,
    first_name   text,
    last_name    text,
    bio          text,
    phone        text
);
CREATE INDEX IF NOT EXISTS user_history_user ON user_history (user_id, replaced_at);

CREATE TABLE IF NOT EXISTS messages (
    chat_id               bigint NOT NULL,
    id                    bigint NOT NULL,
    user_id               bigint,
    date                  timestamptz,
    text                  text,
    reply_to_id           bigint,
    forward_from_user_id  bigint,
    forward_from_chat_id  bigint,
    edit_date             timestamptz,
    media_type            text,
    views                 integer,
    extra                 jsonb NOT NULL DEFAULT '{}',
    ingested_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chat_id, id)
);
-- The two access paths that matter: a chat's timeline (the primary key; ids grow with time within a
-- chat) and everything one user wrote, across chats.
CREATE INDEX IF NOT EXISTS messages_user_date ON messages (user_id, date DESC);
-- Full-text search (/v1/search). 'simple' = no stemming, works for any language.
CREATE INDEX IF NOT EXISTS messages_fts ON messages USING gin (to_tsvector('simple', coalesce(text, '')));

CREATE TABLE IF NOT EXISTS memberships (
    chat_id     bigint NOT NULL,
    user_id     bigint NOT NULL,
    status      text,
    joined_at   timestamptz,
    first_seen  timestamptz,   -- first / last message seen from this user in this chat
    last_seen   timestamptz,
    extra       jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (chat_id, user_id)
);
CREATE INDEX IF NOT EXISTS memberships_user ON memberships (user_id);
