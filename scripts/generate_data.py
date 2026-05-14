from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

SURNAMES = ["赵", "钱", "孙", "李", "周", "吴", "郑", "王", "冯", "陈"]
MALE_NAMES = ["明", "强", "磊", "军", "洋", "勇", "杰", "涛", "超", "峰", "睿", "航"]
FEMALE_NAMES = ["芳", "娜", "敏", "静", "丽", "艳", "雅", "雪", "婷", "宁", "欣", "然"]


def main() -> None:
    parser = argparse.ArgumentParser(description="生成族谱管理系统 CSV 模拟数据")
    parser.add_argument("--out", default="data/generated", help="输出目录")
    parser.add_argument("--large-size", type=int, default=50000, help="第一个大族谱成员数")
    parser.add_argument("--total-size", type=int, default=100000, help="全系统成员总数")
    parser.add_argument("--seed", type=int, default=20260514)
    args = parser.parse_args()

    random.seed(args.seed)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    genealogy_sizes = build_sizes(args.large_size, args.total_size)
    write_users(out_dir)
    genealogy_rows = write_genealogies(out_dir, genealogy_sizes)
    write_members_and_relations(out_dir, genealogy_rows, genealogy_sizes)
    print(f"CSV 已生成到 {out_dir.resolve()}")


def build_sizes(large_size: int, total_size: int) -> list[int]:
    if large_size < 50000:
        raise ValueError("--large-size 至少为 50000")
    if total_size < 100000:
        raise ValueError("--total-size 至少为 100000")
    remaining = total_size - large_size
    base = remaining // 9
    sizes = [large_size] + [base] * 9
    sizes[-1] += remaining - base * 9
    return sizes


def write_users(out_dir: Path) -> None:
    with (out_dir / "users.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "username", "password_hash"])
        writer.writerow([1, "admin", "pbkdf2:sha256:placeholder"])
        for i in range(2, 12):
            writer.writerow([i, f"user{i}", "pbkdf2:sha256:placeholder"])


def write_genealogies(out_dir: Path, sizes: list[int]) -> list[dict[str, int | str]]:
    rows = []
    with (out_dir / "genealogies.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "surname", "revision_time", "creator_user_id"])
        for i, surname in enumerate(SURNAMES, start=1):
            row = {
                "id": i,
                "name": f"{surname}氏模拟族谱",
                "surname": surname,
                "revision_time": "2026-05-14",
                "creator_user_id": 1 + (i % 10),
                "size": sizes[i - 1],
            }
            rows.append(row)
            writer.writerow([row["id"], row["name"], row["surname"], row["revision_time"], row["creator_user_id"]])
    return rows


def write_members_and_relations(
    out_dir: Path,
    genealogies: list[dict[str, int | str]],
    sizes: list[int],
) -> None:
    member_id = 1
    marriage_id = 1
    with (out_dir / "members.csv").open("w", newline="", encoding="utf-8") as members_f, (
        out_dir / "parent_child_relations.csv"
    ).open("w", newline="", encoding="utf-8") as relations_f, (
        out_dir / "marriages.csv"
    ).open("w", newline="", encoding="utf-8") as marriages_f:
        members = csv.writer(members_f)
        relations = csv.writer(relations_f)
        marriages = csv.writer(marriages_f)
        members.writerow(["id", "genealogy_id", "name", "gender", "birth_year", "death_year", "generation", "biography"])
        relations.writerow(["parent_id", "child_id", "relation_type"])
        marriages.writerow(["id", "member1_id", "member2_id", "married_year", "ended_year", "status"])

        for genealogy, size in zip(genealogies, sizes):
            surname = str(genealogy["surname"])
            genealogy_id = int(genealogy["id"])
            generation_members: list[list[int]] = []
            remaining = size
            for generation in range(1, 31):
                generations_left = 31 - generation
                generation_size = max(2, remaining // generations_left)
                if generation == 30:
                    generation_size = remaining
                current_generation: list[int] = []
                for index in range(generation_size):
                    gender = "M" if index % 2 == 0 else "F"
                    name_pool = MALE_NAMES if gender == "M" else FEMALE_NAMES
                    birth_year = 1850 + generation * 24 + random.randint(-3, 3)
                    death_year = birth_year + random.randint(62, 88) if birth_year < 1945 else ""
                    name = f"{surname}{name_pool[index % len(name_pool)]}{generation}"
                    members.writerow([member_id, genealogy_id, name, gender, birth_year, death_year, generation, "模拟生成成员"])
                    current_generation.append(member_id)
                    member_id += 1
                generation_members.append(current_generation)
                remaining -= generation_size

                if generation > 1:
                    parents = generation_members[generation - 2]
                    fathers = parents[0::2] or parents
                    mothers = parents[1::2] or parents
                    for offset, child_id in enumerate(current_generation):
                        father_id = fathers[offset % len(fathers)]
                        mother_id = mothers[offset % len(mothers)]
                        relations.writerow([father_id, child_id, "father"])
                        relations.writerow([mother_id, child_id, "mother"])

                for left, right in zip(current_generation[0::2], current_generation[1::2]):
                    marriages.writerow([marriage_id, left, right, 1870 + generation * 24, "", "active"])
                    marriage_id += 1


if __name__ == "__main__":
    main()
