# 数据生成、导入与导出

## 1. 生成模拟数据

生成至少 10 个族谱、总成员不少于 100000 人、其中一个族谱不少于 50000 人：

```bash
python scripts/generate_data.py --out data/generated --large-size 50000 --total-size 100000
```

脚本输出：

| 文件 | 目标表 |
| --- | --- |
| `users.csv` | `users` |
| `genealogies.csv` | `genealogies` |
| `members.csv` | `members` |
| `parent_child_relations.csv` | `parent_child_relations` |
| `marriages.csv` | `marriages` |

脚本会让第一个族谱拥有 `--large-size` 指定的人数，默认 50,000；其余 9 个族谱在剩余人数中随机分配。脚本为每个族谱生成 30 代传承；第 2 代开始，每个成员至少拥有父母关系，因此族谱内成员满足亲缘连接要求。

## 2. SQLite 导入

SQLite 可用于本系统本地演示：

```bash
sqlite3 genealogy.db
```

父母子女关系和婚姻关系依赖外键，建议先导入主表。由于 SQLite 的 `.import` 会把 CSV 空字段作为空字符串，含可空整数列的表建议先导入暂存表，再把空字符串转换为 `NULL`：

```sql
.mode csv
.import --skip 1 data/generated/users.csv users
.import --skip 1 data/generated/genealogies.csv genealogies

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

.import --skip 1 data/generated/parent_child_relations.csv parent_child_relations

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
```

注意：`users.csv` 中只有 `admin` 用户写入了 `admin123` 对应的有效密码 hash。导入后可直接使用 `admin / admin123` 登录；`user2` 到 `user11` 仍保留占位密码 hash。

## 3. MySQL LOAD DATA 示例

```sql
LOAD DATA LOCAL INFILE 'data/generated/members.csv'
INTO TABLE members
FIELDS TERMINATED BY ','
ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES
(id, genealogy_id, name, gender, birth_year, death_year, generation, biography);
```

其他表同理，按外键依赖顺序导入：`users` -> `genealogies` -> `members` -> `parent_child_relations` -> `marriages`。

## 4. PostgreSQL COPY 示例

```sql
\copy members(id, genealogy_id, name, gender, birth_year, death_year, generation, biography)
FROM 'data/generated/members.csv'
WITH (FORMAT csv, HEADER true);
```

导入顺序同 MySQL。

## 5. 导出某分支备份

Web 界面里进入族谱详情页，树形预览选择根成员后点击“导出当前分支 CSV”。系统会在 `exports/` 下写出文件。

也可以直接访问：

```text
/genealogies/<genealogy_id>/export?root_id=<member_id>
```
