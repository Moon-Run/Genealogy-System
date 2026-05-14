-- 1. 给定成员 ID，查询其配偶及所有子女。
-- SQLite 参数写法为 :member_id；PostgreSQL/MySQL 可替换为 ? 或 $1。
SELECT 'spouse' AS relation, m.id, m.name, m.gender, m.birth_year, m.death_year, s.married_year
FROM marriages s
JOIN members m ON m.id = CASE
    WHEN s.member1_id = :member_id THEN s.member2_id
    ELSE s.member1_id
END
WHERE s.member1_id = :member_id OR s.member2_id = :member_id
UNION ALL
SELECT 'child' AS relation, c.id, c.name, c.gender, c.birth_year, c.death_year, NULL AS married_year
FROM parent_child_relations r
JOIN members c ON c.id = r.child_id
WHERE r.parent_id = :member_id
ORDER BY relation, birth_year, id;

-- 2. Recursive CTE：输入成员 A 的 ID，输出其向上追溯的所有历代祖先。
WITH RECURSIVE ancestors(id, name, gender, birth_year, death_year, generation, depth, relation_type) AS (
    SELECT p.id, p.name, p.gender, p.birth_year, p.death_year, p.generation, 1, r.relation_type
    FROM parent_child_relations r
    JOIN members p ON p.id = r.parent_id
    WHERE r.child_id = :member_id
    UNION ALL
    SELECT gp.id, gp.name, gp.gender, gp.birth_year, gp.death_year, gp.generation,
           a.depth + 1, r.relation_type
    FROM ancestors a
    JOIN parent_child_relations r ON r.child_id = a.id
    JOIN members gp ON gp.id = r.parent_id
)
SELECT DISTINCT *
FROM ancestors
ORDER BY depth, relation_type, birth_year, id;

-- 3. 统计某个家族中平均寿命最长的一代人。
SELECT generation,
       COUNT(*) AS member_count,
       AVG(COALESCE(death_year, CAST(strftime('%Y', 'now') AS INTEGER)) - birth_year) AS avg_lifespan
FROM members
WHERE genealogy_id = :genealogy_id
  AND birth_year IS NOT NULL
GROUP BY generation
ORDER BY avg_lifespan DESC
LIMIT 1;

-- 4. 查询所有年龄超过 50 岁、且没有配偶的男性成员。
SELECT m.*
FROM members m
WHERE m.genealogy_id = :genealogy_id
  AND m.gender = 'M'
  AND m.birth_year IS NOT NULL
  AND CAST(strftime('%Y', 'now') AS INTEGER) - m.birth_year > 50
  AND NOT EXISTS (
      SELECT 1
      FROM marriages s
      WHERE s.member1_id = m.id OR s.member2_id = m.id
  )
ORDER BY m.birth_year, m.id;

-- 5. 找出家族中出生年份早于该辈分平均出生年份的所有成员。
WITH generation_avg AS (
    SELECT genealogy_id, generation, AVG(birth_year) AS avg_birth_year
    FROM members
    WHERE birth_year IS NOT NULL
    GROUP BY genealogy_id, generation
)
SELECT m.*, g.avg_birth_year
FROM members m
JOIN generation_avg g
  ON g.genealogy_id = m.genealogy_id
 AND g.generation = m.generation
WHERE m.genealogy_id = :genealogy_id
  AND m.birth_year IS NOT NULL
  AND m.birth_year < g.avg_birth_year
ORDER BY m.generation, m.birth_year, m.id;

-- 6. 查询某曾祖父的所有曾孙，四代查询示例。
SELECT great_grandchild.*
FROM parent_child_relations r1
JOIN parent_child_relations r2 ON r2.parent_id = r1.child_id
JOIN parent_child_relations r3 ON r3.parent_id = r2.child_id
JOIN members great_grandchild ON great_grandchild.id = r3.child_id
WHERE r1.parent_id = :ancestor_id
ORDER BY great_grandchild.birth_year, great_grandchild.id;

-- 7. 两个成员之间的亲缘或婚姻链路，限制搜索深度为 20。
WITH RECURSIVE graph(from_id, to_id, label) AS (
    SELECT parent_id, child_id, relation_type || '->child' FROM parent_child_relations
    UNION ALL
    SELECT child_id, parent_id, 'child->' || relation_type FROM parent_child_relations
    UNION ALL
    SELECT member1_id, member2_id, 'spouse' FROM marriages
    UNION ALL
    SELECT member2_id, member1_id, 'spouse' FROM marriages
),
search(id, path, labels, depth) AS (
    SELECT :member_a_id, printf('%d', :member_a_id), '', 0
    UNION ALL
    SELECT g.to_id,
           search.path || ',' || g.to_id,
           CASE
               WHEN search.labels = '' THEN g.label
               ELSE search.labels || ' -> ' || g.label
           END,
           search.depth + 1
    FROM search
    JOIN graph g ON g.from_id = search.id
    WHERE search.depth < 20
      AND instr(',' || search.path || ',', ',' || g.to_id || ',') = 0
)
SELECT depth, path, labels
FROM search
WHERE id = :member_b_id
ORDER BY depth
LIMIT 1;
