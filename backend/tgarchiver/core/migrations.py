"""Versioned SQL migrations. Append new entries; never edit applied ones. Index + 1 = PRAGMA user_version."""

MIGRATIONS: list[str] = [
    # 1: base schema (docs/11-data-model.md)
    """
    CREATE TABLE accounts(id INTEGER PRIMARY KEY, tg_user_id INTEGER, phone TEXT, name TEXT, username TEXT,
      created_at TEXT);
    CREATE TABLE chats(id INTEGER PRIMARY KEY, account_id INT, access_hash INT, type TEXT, title TEXT,
      username TEXT, is_forum INT DEFAULT 0, is_archived INT DEFAULT 0, unread_count INT DEFAULT 0,
      unread_mentions INT DEFAULT 0, read_inbox_max_id INT DEFAULT 0, last_message_id INT DEFAULT 0,
      last_message_at TEXT, photo_path TEXT, noforwards INT DEFAULT 0, phone TEXT, is_contact INT DEFAULT 0,
      linked_chat_id INT, raw JSON, updated_at TEXT);
    CREATE INDEX ix_chats_type ON chats(type);
    CREATE TABLE topics(id INT, chat_id INT, title TEXT, icon TEXT, top_message INT, unread_count INT DEFAULT 0,
      PRIMARY KEY(chat_id, id));
    CREATE TABLE users(id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, username TEXT, phone TEXT,
      is_bot INT DEFAULT 0, raw JSON);
    CREATE TABLE messages(chat_id INT, id INT, topic_id INT DEFAULT 0, sender_id INT, out INT DEFAULT 0,
      date TEXT, edit_date TEXT, text TEXT, reply_to INT, fwd_from JSON, media_type TEXT, has_media INT DEFAULT 0,
      has_link INT DEFAULT 0, reactions JSON, buttons JSON, service_action TEXT, noforwards INT DEFAULT 0,
      grouped_id INT, raw JSON, PRIMARY KEY(chat_id, id));
    CREATE INDEX ix_msg_date ON messages(chat_id, date);
    CREATE INDEX ix_msg_sender ON messages(sender_id);
    CREATE VIRTUAL TABLE messages_fts USING fts5(text, extra, tokenize='unicode61 remove_diacritics 2');
    CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
      INSERT INTO messages_fts(rowid, text, extra) VALUES (new.rowid, coalesce(new.text, ''), '');
    END;
    CREATE TRIGGER messages_au AFTER UPDATE OF text ON messages BEGIN
      UPDATE messages_fts SET text = coalesce(new.text, '') WHERE rowid = new.rowid;
    END;
    CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
      DELETE FROM messages_fts WHERE rowid = old.rowid;
    END;
    CREATE TABLE media(id INTEGER PRIMARY KEY, chat_id INT, message_id INT, tg_file_id INT, type TEXT,
      mime TEXT, size INT DEFAULT 0, file_name TEXT, path TEXT, thumb_path TEXT, bytes_done INT DEFAULT 0,
      sha256 TEXT, status TEXT DEFAULT 'pending', priority INT DEFAULT 0, attempts INT DEFAULT 0, error TEXT,
      duration REAL, date TEXT, updated_at TEXT, UNIQUE(chat_id, message_id));
    CREATE INDEX ix_media_status ON media(status, priority);
    CREATE INDEX ix_media_file ON media(tg_file_id);
    CREATE TRIGGER media_ai AFTER INSERT ON media WHEN new.file_name IS NOT NULL BEGIN
      UPDATE messages_fts SET extra = extra || ' ' || new.file_name
       WHERE rowid = (SELECT rowid FROM messages WHERE chat_id = new.chat_id AND id = new.message_id);
    END;
    CREATE TABLE transcripts(media_id INT PRIMARY KEY, lang TEXT, model TEXT, text TEXT, txt_path TEXT,
      srt_path TEXT, created_at TEXT);
    CREATE TRIGGER transcripts_ai AFTER INSERT ON transcripts BEGIN
      UPDATE messages_fts SET extra = extra || ' ' || coalesce(new.text, '')
       WHERE rowid = (SELECT m.rowid FROM messages m JOIN media d ON d.chat_id = m.chat_id AND d.message_id = m.id
                      WHERE d.id = new.media_id);
    END;
    CREATE TABLE sync_state(chat_id INT, topic_id INT DEFAULT 0, last_message_id INT DEFAULT 0, done INT DEFAULT 0,
      total INT DEFAULT 0, updated_at TEXT, PRIMARY KEY(chat_id, topic_id));
    CREATE TABLE jobs(id INTEGER PRIMARY KEY, kind TEXT, title TEXT, params JSON, status TEXT, progress JSON,
      checkpoint JSON, error TEXT, attempts INT DEFAULT 0, wait_until TEXT, parent_id INT,
      created_at TEXT, updated_at TEXT);
    CREATE INDEX ix_jobs_status ON jobs(status);
    CREATE TABLE folders(id INTEGER PRIMARY KEY, name TEXT, color TEXT, emoji TEXT, parent_id INT, source TEXT,
      tg_filter_id INT, position INT DEFAULT 0);
    CREATE TABLE folder_chats(folder_id INT, chat_id INT, PRIMARY KEY(folder_id, chat_id));
    CREATE TABLE presets(id INTEGER PRIMARY KEY, kind TEXT, name TEXT, data JSON, updated_at TEXT);
    CREATE TABLE mini_apps(id INTEGER PRIMARY KEY, bot_id INT, bot_username TEXT, kind TEXT, short_name TEXT,
      title TEXT, url TEXT, found_in_chat INT, found_in_msg INT, first_seen TEXT, last_seen TEXT,
      UNIQUE(bot_id, kind, short_name, url));
    CREATE TABLE mini_app_snapshots(id INTEGER PRIMARY KEY, mini_app_id INT, path TEXT, mode TEXT,
      states INT DEFAULT 0, created_at TEXT);
    CREATE TABLE ai_reports(id INTEGER PRIMARY KEY, kind TEXT, scope JSON, provider TEXT, model TEXT,
      result JSON, tokens_in INT, tokens_out INT, created_at TEXT);
    CREATE TABLE exports(chat_id INT, format TEXT, split TEXT, path TEXT, parts INT, messages INT,
      last_message_id INT, created_at TEXT, PRIMARY KEY(chat_id, format, split));
    CREATE TABLE kv(key TEXT PRIMARY KEY, value JSON);
    """,
    # 2: cached per-chat counters so the chat list stays < 300 ms with 2000 chats (docs/17)
    """
    ALTER TABLE chats ADD COLUMN stored_messages INT DEFAULT 0;
    ALTER TABLE chats ADD COLUMN media_count INT DEFAULT 0;
    ALTER TABLE chats ADD COLUMN media_done INT DEFAULT 0;
    """,
]
