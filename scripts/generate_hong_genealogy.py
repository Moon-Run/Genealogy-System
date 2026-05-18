from __future__ import annotations

import argparse
import random
import sqlite3
from pathlib import Path

GENEALOGY_NAME = "洪氏族谱"
LEGACY_GENEALOGY_NAME = "洪氏优化族谱"

MALE_SEEDS = ["浩洋", "加润", "承志", "嘉树", "明远", "景行", "子昂", "怀瑾", "修齐", "思源", "启航", "文博"]
FEMALE_SEEDS = ["清妍", "若宁", "雅慧", "诗涵", "婉仪", "静姝", "可欣", "宛晴", "语桐", "舒然"]

MALE_FIRST_CHARS = "浩嘉承明景子怀修思启文泽睿俊博宇弘彦绍远靖书廷冠立知尚哲煜铭昊晨皓凯卓予恒济世安秉维致"
MALE_SECOND_CHARS = "洋润志树远行昂瑾齐源航博然轩辰宁泽铭宇杰熙宸峰川朗谦晟尧庭岳洲翰越琛祺毅钧"
FEMALE_FIRST_CHARS = "清若雅诗婉静可宛语舒芷雨梦依佳思云月沐晓瑾书以知宁欣柔映安亦采"
FEMALE_SECOND_CHARS = "妍宁慧涵仪姝欣晴桐然瑶琳萱琪雯婧怡彤岚玥昕洁蓉薇璇悦"


def build_name_pool(seeds: list[str], first_chars: str, second_chars: str) -> list[str]:
    names = list(dict.fromkeys(seeds))
    for first in first_chars:
        for second in second_chars:
            candidate = f"{first}{second}"
            if candidate not in names:
                names.append(candidate)
    return names


MALE_NAMES = build_name_pool(MALE_SEEDS, MALE_FIRST_CHARS, MALE_SECOND_CHARS)
FEMALE_NAMES = build_name_pool(FEMALE_SEEDS, FEMALE_FIRST_CHARS, FEMALE_SECOND_CHARS)


def generation_sizes(total: int, generations: int = 30) -> list[int]:
    if total < 50000:
        raise ValueError("洪氏大族谱成员数至少需要 50000")
    weights = [0] + [generation * generation for generation in range(2, generations + 1)]
    remaining = total - 1
    raw_sizes = [1]
    allocated = 0
    weight_sum = sum(weights)
    for generation in range(2, generations + 1):
        size = max(4, int(remaining * weights[generation - 1] / weight_sum))
        if size % 2 == 1:
            size += 1
        raw_sizes.append(size)
        allocated += size

    delta = total - (1 + allocated)
    raw_sizes[-1] += delta
    if raw_sizes[-1] % 2 == 1:
        raw_sizes[-1] += 1
        raw_sizes[-2] -= 1
    return raw_sizes


def insert_hong_genealogy(database: Path, size: int, replace: bool, seed: int) -> int:
    random.seed(seed)
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")

    with conn:
        admin = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        if admin is None:
            raise RuntimeError("请先初始化数据库并创建 admin 用户")
        admin_id = admin["id"]

        existing = conn.execute(
            """
            SELECT id
            FROM genealogies
            WHERE creator_user_id = ?
              AND name IN (?, ?)
            ORDER BY CASE WHEN name = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (admin_id, GENEALOGY_NAME, LEGACY_GENEALOGY_NAME, GENEALOGY_NAME),
        ).fetchone()
        if existing and not replace:
            raise RuntimeError(f"{GENEALOGY_NAME} 已存在；如需重建请添加 --replace")
        if existing and replace:
            conn.execute("DELETE FROM genealogies WHERE id = ?", (existing["id"],))

        cursor = conn.execute(
            """
            INSERT INTO genealogies (name, surname, revision_time, creator_user_id)
            VALUES (?, ?, ?, ?)
            """,
            (GENEALOGY_NAME, "洪", "2026-05-18", admin_id),
        )
        genealogy_id = cursor.lastrowid

        sizes = generation_sizes(size)
        generations: list[list[dict[str, int | str]]] = []
        member_rows = []
        relation_rows = []
        marriage_rows = []

        next_member_id = int(conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM members").fetchone()[0])
        next_marriage_id = int(conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM marriages").fetchone()[0])

        for generation, generation_size in enumerate(sizes, start=1):
            birth_base = 1280 + (generation - 1) * 24
            current_generation: list[dict[str, int | str]] = []
            for index in range(generation_size):
                if generation == 1:
                    gender = "M"
                    given = "始祖"
                else:
                    gender = "M" if index % 2 == 0 else "F"
                    pool = MALE_NAMES if gender == "M" else FEMALE_NAMES
                    given = pool[(index + generation) % len(pool)]
                birth_year = birth_base + random.randint(-2, 2)
                death_year: int | None = None
                if birth_year <= 1935:
                    death_year = birth_year + random.randint(58, 90)
                member_id = next_member_id
                next_member_id += 1
                member = {"id": member_id, "gender": gender, "birth_year": birth_year}
                current_generation.append(member)
                member_rows.append(
                    (
                        member_id,
                        genealogy_id,
                        f"洪{given}",
                        gender,
                        birth_year,
                        death_year,
                        generation,
                        "洪氏族谱模拟成员：所有成员沿同一始祖树状扩展。",
                    )
                )

            if generation == 2:
                root_id = generations[0][0]["id"]
                for child in current_generation:
                    relation_rows.append((root_id, child["id"], "father"))
            elif generation > 2:
                parents = generations[-1]
                fathers = [item for item in parents if item["gender"] == "M"] or parents
                mothers = [item for item in parents if item["gender"] == "F"] or parents
                for index, child in enumerate(current_generation):
                    father = fathers[index % len(fathers)]
                    mother = mothers[(index // 2) % len(mothers)]
                    relation_rows.append((father["id"], child["id"], "father"))
                    if mother["id"] != father["id"]:
                        relation_rows.append((mother["id"], child["id"], "mother"))

            if generation > 1:
                males = [item for item in current_generation if item["gender"] == "M"]
                females = [item for item in current_generation if item["gender"] == "F"]
                for male, female in zip(males, females):
                    marriage_rows.append(
                        (
                            next_marriage_id,
                            min(male["id"], female["id"]),
                            max(male["id"], female["id"]),
                            max(male["birth_year"], female["birth_year"]) + 22,
                            None,
                            "active",
                        )
                    )
                    next_marriage_id += 1

            generations.append(current_generation)

        conn.executemany(
            """
            INSERT INTO members
                (id, genealogy_id, name, gender, birth_year, death_year, generation, biography)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            member_rows,
        )
        conn.executemany(
            """
            INSERT INTO parent_child_relations (parent_id, child_id, relation_type)
            VALUES (?, ?, ?)
            """,
            relation_rows,
        )
        conn.executemany(
            """
            INSERT INTO marriages (id, member1_id, member2_id, married_year, ended_year, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            marriage_rows,
        )

    conn.close()
    return genealogy_id


def main() -> None:
    parser = argparse.ArgumentParser(description="生成并导入结构更规整的洪氏大族谱")
    parser.add_argument("--database", default="genealogy.db")
    parser.add_argument("--size", type=int, default=52000)
    parser.add_argument("--seed", type=int, default=20260518)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    genealogy_id = insert_hong_genealogy(Path(args.database), args.size, args.replace, args.seed)
    print(f"{GENEALOGY_NAME}已生成：genealogy_id={genealogy_id}, size={args.size}")


if __name__ == "__main__":
    main()
