PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS genealogies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    surname TEXT NOT NULL,
    revision_time TEXT NOT NULL,
    creator_user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (creator_user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE (creator_user_id, name)
);

CREATE TABLE IF NOT EXISTS genealogy_collaborators (
    genealogy_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT 'editor' CHECK (role IN ('editor', 'viewer')),
    invited_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (genealogy_id, user_id),
    FOREIGN KEY (genealogy_id) REFERENCES genealogies(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    genealogy_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    gender TEXT NOT NULL CHECK (gender IN ('M', 'F', 'U')),
    birth_year INTEGER CHECK (birth_year IS NULL OR birth_year BETWEEN 1 AND 9999),
    death_year INTEGER CHECK (
        death_year IS NULL
        OR (death_year BETWEEN 1 AND 9999 AND (birth_year IS NULL OR death_year >= birth_year))
    ),
    generation INTEGER NOT NULL DEFAULT 1 CHECK (generation > 0),
    biography TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (genealogy_id) REFERENCES genealogies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS parent_child_relations (
    parent_id INTEGER NOT NULL,
    child_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL CHECK (relation_type IN ('father', 'mother')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (parent_id, child_id, relation_type),
    FOREIGN KEY (parent_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (child_id) REFERENCES members(id) ON DELETE CASCADE,
    CHECK (parent_id <> child_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_child_father
ON parent_child_relations(child_id)
WHERE relation_type = 'father';

CREATE UNIQUE INDEX IF NOT EXISTS ux_child_mother
ON parent_child_relations(child_id)
WHERE relation_type = 'mother';

CREATE TABLE IF NOT EXISTS marriages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member1_id INTEGER NOT NULL,
    member2_id INTEGER NOT NULL,
    married_year INTEGER CHECK (married_year IS NULL OR married_year BETWEEN 1 AND 9999),
    ended_year INTEGER CHECK (
        ended_year IS NULL
        OR (ended_year BETWEEN 1 AND 9999 AND (married_year IS NULL OR ended_year >= married_year))
    ),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'divorced', 'widowed')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (member1_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (member2_id) REFERENCES members(id) ON DELETE CASCADE,
    CHECK (member1_id <> member2_id),
    CHECK (member1_id < member2_id),
    UNIQUE (member1_id, member2_id)
);

CREATE INDEX IF NOT EXISTS idx_genealogies_creator ON genealogies(creator_user_id);
CREATE INDEX IF NOT EXISTS idx_collaborators_user ON genealogy_collaborators(user_id);
CREATE INDEX IF NOT EXISTS idx_members_genealogy ON members(genealogy_id);
CREATE INDEX IF NOT EXISTS idx_members_name ON members(name);
CREATE INDEX IF NOT EXISTS idx_members_genealogy_name ON members(genealogy_id, name);
CREATE INDEX IF NOT EXISTS idx_members_generation ON members(genealogy_id, generation);
CREATE INDEX IF NOT EXISTS idx_members_genealogy_generation_birth ON members(genealogy_id, generation, birth_year, id);
CREATE INDEX IF NOT EXISTS idx_members_life_stats ON members(genealogy_id, gender, birth_year, death_year);
CREATE INDEX IF NOT EXISTS idx_parent_child_parent ON parent_child_relations(parent_id, child_id);
CREATE INDEX IF NOT EXISTS idx_parent_child_child ON parent_child_relations(child_id, parent_id);
CREATE INDEX IF NOT EXISTS idx_parent_child_child_type ON parent_child_relations(child_id, relation_type, parent_id);
CREATE INDEX IF NOT EXISTS idx_marriages_member1 ON marriages(member1_id);
CREATE INDEX IF NOT EXISTS idx_marriages_member2 ON marriages(member2_id);
CREATE INDEX IF NOT EXISTS idx_marriages_pair_status ON marriages(member1_id, member2_id, status);

CREATE VIRTUAL TABLE IF NOT EXISTS members_fts
USING fts5(name, biography, content='members', content_rowid='id', tokenize='unicode61');

INSERT OR REPLACE INTO members_fts(rowid, name, biography)
SELECT id, name, biography FROM members;

CREATE TRIGGER IF NOT EXISTS trg_members_updated_at
AFTER UPDATE ON members
FOR EACH ROW
BEGIN
    UPDATE members SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_members_fts_insert
AFTER INSERT ON members
FOR EACH ROW
BEGIN
    INSERT INTO members_fts(rowid, name, biography)
    VALUES (NEW.id, NEW.name, NEW.biography);
END;

CREATE TRIGGER IF NOT EXISTS trg_members_fts_update
AFTER UPDATE OF name, biography ON members
FOR EACH ROW
BEGIN
    INSERT INTO members_fts(members_fts, rowid, name, biography)
    VALUES('delete', OLD.id, OLD.name, OLD.biography);
    INSERT INTO members_fts(rowid, name, biography)
    VALUES (NEW.id, NEW.name, NEW.biography);
END;

CREATE TRIGGER IF NOT EXISTS trg_members_fts_delete
AFTER DELETE ON members
FOR EACH ROW
BEGIN
    INSERT INTO members_fts(members_fts, rowid, name, biography)
    VALUES('delete', OLD.id, OLD.name, OLD.biography);
END;

CREATE TRIGGER IF NOT EXISTS trg_parent_child_same_genealogy
BEFORE INSERT ON parent_child_relations
FOR EACH ROW
BEGIN
    SELECT
        CASE
            WHEN (SELECT genealogy_id FROM members WHERE id = NEW.parent_id)
               <> (SELECT genealogy_id FROM members WHERE id = NEW.child_id)
            THEN RAISE(ABORT, 'parent and child must belong to the same genealogy')
        END;
END;

CREATE TRIGGER IF NOT EXISTS trg_parent_child_age
BEFORE INSERT ON parent_child_relations
FOR EACH ROW
BEGIN
    SELECT
        CASE
            WHEN (SELECT birth_year FROM members WHERE id = NEW.parent_id) IS NOT NULL
             AND (SELECT birth_year FROM members WHERE id = NEW.child_id) IS NOT NULL
             AND (SELECT birth_year FROM members WHERE id = NEW.parent_id)
                 >= (SELECT birth_year FROM members WHERE id = NEW.child_id)
            THEN RAISE(ABORT, 'parent birth year must be earlier than child birth year')
        END;
END;

CREATE TRIGGER IF NOT EXISTS trg_parent_child_gender
BEFORE INSERT ON parent_child_relations
FOR EACH ROW
BEGIN
    SELECT
        CASE
            WHEN NEW.relation_type = 'father'
             AND (SELECT gender FROM members WHERE id = NEW.parent_id) NOT IN ('M', 'U')
            THEN RAISE(ABORT, 'father relation requires a male or unknown-gender parent')
            WHEN NEW.relation_type = 'mother'
             AND (SELECT gender FROM members WHERE id = NEW.parent_id) NOT IN ('F', 'U')
            THEN RAISE(ABORT, 'mother relation requires a female or unknown-gender parent')
        END;
END;

CREATE TRIGGER IF NOT EXISTS trg_parent_child_no_cycle
BEFORE INSERT ON parent_child_relations
FOR EACH ROW
BEGIN
    SELECT
        CASE
            WHEN EXISTS (
                WITH RECURSIVE descendants(id) AS (
                    SELECT child_id
                    FROM parent_child_relations
                    WHERE parent_id = NEW.child_id
                    UNION ALL
                    SELECT r.child_id
                    FROM parent_child_relations r
                    JOIN descendants d ON d.id = r.parent_id
                )
                SELECT 1 FROM descendants WHERE id = NEW.parent_id
            )
            THEN RAISE(ABORT, 'parent-child relation would create a cycle')
        END;
END;

CREATE TRIGGER IF NOT EXISTS trg_member_birth_update_parent_age
BEFORE UPDATE OF birth_year ON members
FOR EACH ROW
BEGIN
    SELECT
        CASE
            WHEN NEW.birth_year IS NOT NULL
             AND EXISTS (
                SELECT 1
                FROM parent_child_relations r
                JOIN members c ON c.id = r.child_id
                WHERE r.parent_id = NEW.id
                  AND c.birth_year IS NOT NULL
                  AND NEW.birth_year >= c.birth_year
             )
            THEN RAISE(ABORT, 'parent birth year must remain earlier than child birth year')
            WHEN NEW.birth_year IS NOT NULL
             AND EXISTS (
                SELECT 1
                FROM parent_child_relations r
                JOIN members p ON p.id = r.parent_id
                WHERE r.child_id = NEW.id
                  AND p.birth_year IS NOT NULL
                  AND p.birth_year >= NEW.birth_year
             )
            THEN RAISE(ABORT, 'child birth year must remain later than parent birth year')
        END;
END;

CREATE TRIGGER IF NOT EXISTS trg_marriage_same_genealogy
BEFORE INSERT ON marriages
FOR EACH ROW
BEGIN
    SELECT
        CASE
            WHEN (SELECT genealogy_id FROM members WHERE id = NEW.member1_id)
               <> (SELECT genealogy_id FROM members WHERE id = NEW.member2_id)
            THEN RAISE(ABORT, 'spouses must belong to the same genealogy')
        END;
END;
