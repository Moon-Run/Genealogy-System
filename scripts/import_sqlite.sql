.mode csv

CREATE TEMP TABLE users_stage (id, username, password_hash);
.import --skip 1 data/generated/users.csv users_stage
INSERT INTO users(id, username, password_hash)
SELECT id, username, password_hash FROM users_stage;

CREATE TEMP TABLE genealogies_stage (id, name, surname, revision_time, creator_user_id);
.import --skip 1 data/generated/genealogies.csv genealogies_stage
INSERT INTO genealogies(id, name, surname, revision_time, creator_user_id)
SELECT id, name, surname, revision_time, creator_user_id FROM genealogies_stage;

CREATE TEMP TABLE members_stage (
    id, genealogy_id, name, gender, birth_year, death_year, generation, biography
);
.import --skip 1 data/generated/members.csv members_stage
INSERT INTO members(id, genealogy_id, name, gender, birth_year, death_year, generation, biography)
SELECT id, genealogy_id, name, gender,
       NULLIF(birth_year, ''),
       NULLIF(death_year, ''),
       generation,
       biography
FROM members_stage;

CREATE TEMP TABLE pcr_stage (parent_id, child_id, relation_type);
.import --skip 1 data/generated/parent_child_relations.csv pcr_stage
INSERT INTO parent_child_relations(parent_id, child_id, relation_type)
SELECT parent_id, child_id, relation_type FROM pcr_stage;

CREATE TEMP TABLE marriages_stage (
    id, member1_id, member2_id, married_year, ended_year, status
);
.import --skip 1 data/generated/marriages.csv marriages_stage
INSERT INTO marriages(id, member1_id, member2_id, married_year, ended_year, status)
SELECT id, member1_id, member2_id,
       NULLIF(married_year, ''),
       NULLIF(ended_year, ''),
       status
FROM marriages_stage;
