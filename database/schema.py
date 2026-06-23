from __future__ import annotations

from .base import Database
from .vk_meta import apply_vk_schema


CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS ad_groups (
    id SERIAL PRIMARY KEY,
    group_id BIGINT NOT NULL,
    status SMALLINT NOT NULL DEFAULT 1,
    end_date DATE NOT NULL,
    creator_id BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS events_counter (
    id SERIAL PRIMARY KEY,
    type SMALLINT NOT NULL,
    event_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS manual_payments (
    id SERIAL PRIMARY KEY,
    payment_state JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS newsletters (
    id SERIAL PRIMARY KEY,
    creation_time TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    creator_id BIGINT NOT NULL,
    text TEXT NOT NULL,
    expires_at DATE,
    target SMALLINT,
    send_time TIME WITHOUT TIME ZONE,
    is_moderating BOOLEAN,
    file_id TEXT
);

CREATE TABLE IF NOT EXISTS partner_groups (
    id SERIAL PRIMARY KEY,
    group_id BIGINT NOT NULL,
    show_ad_ids INTEGER[] DEFAULT '{}',
    need_groups BIGINT[] DEFAULT '{}',
    partner_type SMALLINT DEFAULT 1,
    creator_id BIGINT,
    region_codes SMALLINT[],
    poster_categories SMALLINT[]
);

CREATE TABLE IF NOT EXISTS partners (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    balance INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS payments (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    type SMALLINT NOT NULL,
    from_user BIGINT NOT NULL,
    to_user BIGINT NOT NULL,
    sum INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS posters (
    id SERIAL PRIMARY KEY,
    file_id TEXT,
    text TEXT NOT NULL,
    status SMALLINT DEFAULT 0 NOT NULL,
    creator_id BIGINT NOT NULL,
    end_date DATE NOT NULL,
    region_codes SMALLINT[],
    topic_id SMALLINT,
    referral_button_name TEXT
);

CREATE TABLE IF NOT EXISTS queue (
    id SERIAL PRIMARY KEY,
    activate_time TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    group_id BIGINT NOT NULL,
    poster_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tg_groups (
    id SERIAL PRIMARY KEY,
    group_id BIGINT NOT NULL,
    sub_type CHAR(50),
    need_groups_id BIGINT[],
    rates JSONB,
    rate_type CHAR(40)
);

CREATE TABLE IF NOT EXISTS user_requests (
    id SERIAL PRIMARY KEY,
    type SMALLINT NOT NULL,
    user_id BIGINT NOT NULL,
    comment TEXT,
    amount INTEGER,
    status SMALLINT
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    status CHAR(3),
    referral_user_id BIGINT
);

CREATE TABLE IF NOT EXISTS users_subs_info (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    group_id BIGINT NOT NULL,
    type CHAR(50) NOT NULL,
    expires_at TIMESTAMP WITHOUT TIME ZONE,
    msg_left INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS users_user_id_uidx ON users(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS partners_user_id_uidx ON partners(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS partner_groups_group_id_uidx ON partner_groups(group_id);
"""


async def ensure_schema() -> None:
    db = Database()
    await db.connect()
    for statement in [part.strip() for part in CORE_SCHEMA.split(";") if part.strip()]:
        await db.execute(statement + ";")
    await apply_vk_schema()
