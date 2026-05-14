from __future__ import annotations

import csv
import os
import sqlite3
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "genealogy.db"


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-genealogy-secret")
    app.config["DATABASE"] = os.environ.get("DATABASE", str(DATABASE))
    register_cli(app)

    @app.before_request
    def load_logged_in_user() -> None:
        user_id = session.get("user_id")
        g.user = None
        if user_id is not None:
            g.user = query_one("SELECT id, username FROM users WHERE id = ?", (user_id,))

    @app.teardown_appcontext
    def close_db(_: BaseException | None = None) -> None:
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.route("/")
    def index():
        if g.user:
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/register", methods=("GET", "POST"))
    def register():
        if request.method == "POST":
            username = request.form["username"].strip()
            password = request.form["password"]
            if not username or not password:
                flash("用户名和密码不能为空。", "error")
            elif query_one("SELECT id FROM users WHERE username = ?", (username,)):
                flash("用户名已存在。", "error")
            else:
                execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    (username, generate_password_hash(password)),
                )
                flash("注册成功，请登录。", "success")
                return redirect(url_for("login"))
        return render_template("auth.html", mode="register")

    @app.route("/login", methods=("GET", "POST"))
    def login():
        if request.method == "POST":
            username = request.form["username"].strip()
            password = request.form["password"]
            user = query_one("SELECT * FROM users WHERE username = ?", (username,))
            if user is None or not check_password_hash(user["password_hash"], password):
                flash("用户名或密码错误。", "error")
            else:
                session.clear()
                session["user_id"] = user["id"]
                return redirect(url_for("dashboard"))
        return render_template("auth.html", mode="login")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        genealogies = accessible_genealogies()
        stats = []
        for genealogy in genealogies:
            stats.append(
                {
                    "genealogy": genealogy,
                    "total": query_value(
                        "SELECT COUNT(*) FROM members WHERE genealogy_id = ?",
                        (genealogy["id"],),
                    ),
                    "male": query_value(
                        "SELECT COUNT(*) FROM members WHERE genealogy_id = ? AND gender = 'M'",
                        (genealogy["id"],),
                    ),
                    "female": query_value(
                        "SELECT COUNT(*) FROM members WHERE genealogy_id = ? AND gender = 'F'",
                        (genealogy["id"],),
                    ),
                }
            )
        return render_template("dashboard.html", stats=stats)

    @app.route("/genealogies/create", methods=("POST",))
    @login_required
    def create_genealogy():
        name = request.form["name"].strip()
        surname = request.form["surname"].strip()
        revision_time = request.form["revision_time"].strip()
        if not name or not surname or not revision_time:
            flash("谱名、姓氏和修谱时间不能为空。", "error")
        else:
            execute(
                """
                INSERT INTO genealogies (name, surname, revision_time, creator_user_id)
                VALUES (?, ?, ?, ?)
                """,
                (name, surname, revision_time, g.user["id"]),
            )
            flash("族谱已创建。", "success")
        return redirect(url_for("dashboard"))

    @app.route("/genealogies/<int:genealogy_id>")
    @login_required
    def genealogy_detail(genealogy_id: int):
        genealogy = require_genealogy_access(genealogy_id)
        keyword = request.args.get("q", "").strip()
        if keyword:
            members = query_all(
                """
                SELECT * FROM members
                WHERE genealogy_id = ? AND name LIKE ?
                ORDER BY generation, birth_year, id
                LIMIT 200
                """,
                (genealogy_id, f"%{keyword}%"),
            )
        else:
            members = query_all(
                """
                SELECT * FROM members
                WHERE genealogy_id = ?
                ORDER BY generation, birth_year, id
                LIMIT 200
                """,
                (genealogy_id,),
            )
        total = query_value("SELECT COUNT(*) FROM members WHERE genealogy_id = ?", (genealogy_id,))
        tree = descendants_tree(genealogy_id, request.args.get("root_id", type=int))
        return render_template(
            "genealogy.html",
            genealogy=genealogy,
            members=members,
            keyword=keyword,
            total=total,
            tree=tree,
        )

    @app.route("/genealogies/<int:genealogy_id>/invite", methods=("POST",))
    @login_required
    def invite_user(genealogy_id: int):
        genealogy = require_genealogy_access(genealogy_id, owner_only=True)
        username = request.form["username"].strip()
        user = query_one("SELECT id FROM users WHERE username = ?", (username,))
        if user is None:
            flash("没有找到该用户。", "error")
        elif user["id"] == genealogy["creator_user_id"]:
            flash("创建者已经拥有权限。", "error")
        else:
            execute(
                """
                INSERT OR IGNORE INTO genealogy_collaborators (genealogy_id, user_id)
                VALUES (?, ?)
                """,
                (genealogy_id, user["id"]),
            )
            flash("邀请成功。", "success")
        return redirect(url_for("genealogy_detail", genealogy_id=genealogy_id))

    @app.route("/genealogies/<int:genealogy_id>/members/create", methods=("POST",))
    @login_required
    def create_member(genealogy_id: int):
        require_genealogy_access(genealogy_id)
        data = member_form_data()
        execute(
            """
            INSERT INTO members
                (genealogy_id, name, gender, birth_year, death_year, generation, biography)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                genealogy_id,
                data["name"],
                data["gender"],
                data["birth_year"],
                data["death_year"],
                data["generation"],
                data["biography"],
            ),
        )
        flash("成员已添加。", "success")
        return redirect(url_for("genealogy_detail", genealogy_id=genealogy_id))

    @app.route("/genealogies/<int:genealogy_id>/members/<int:member_id>", methods=("GET", "POST"))
    @login_required
    def member_detail(genealogy_id: int, member_id: int):
        genealogy = require_genealogy_access(genealogy_id)
        member = require_member(genealogy_id, member_id)
        if request.method == "POST":
            data = member_form_data()
            execute(
                """
                UPDATE members
                SET name = ?, gender = ?, birth_year = ?, death_year = ?,
                    generation = ?, biography = ?
                WHERE id = ? AND genealogy_id = ?
                """,
                (
                    data["name"],
                    data["gender"],
                    data["birth_year"],
                    data["death_year"],
                    data["generation"],
                    data["biography"],
                    member_id,
                    genealogy_id,
                ),
            )
            flash("成员信息已更新。", "success")
            return redirect(url_for("member_detail", genealogy_id=genealogy_id, member_id=member_id))

        parents = query_all(
            """
            SELECT p.*, r.relation_type
            FROM parent_child_relations r
            JOIN members p ON p.id = r.parent_id
            WHERE r.child_id = ?
            ORDER BY r.relation_type
            """,
            (member_id,),
        )
        children = query_all(
            """
            SELECT c.*, r.relation_type
            FROM parent_child_relations r
            JOIN members c ON c.id = r.child_id
            WHERE r.parent_id = ?
            ORDER BY c.birth_year, c.id
            """,
            (member_id,),
        )
        spouses = spouses_for(member_id)
        all_members = query_all(
            "SELECT id, name, generation, birth_year FROM members WHERE genealogy_id = ? ORDER BY generation, id",
            (genealogy_id,),
        )
        return render_template(
            "member.html",
            genealogy=genealogy,
            member=member,
            parents=parents,
            children=children,
            spouses=spouses,
            all_members=all_members,
        )

    @app.route("/genealogies/<int:genealogy_id>/members/<int:member_id>/delete", methods=("POST",))
    @login_required
    def delete_member(genealogy_id: int, member_id: int):
        require_genealogy_access(genealogy_id)
        execute("DELETE FROM members WHERE id = ? AND genealogy_id = ?", (member_id, genealogy_id))
        flash("成员已删除。", "success")
        return redirect(url_for("genealogy_detail", genealogy_id=genealogy_id))

    @app.route("/genealogies/<int:genealogy_id>/relations/parent", methods=("POST",))
    @login_required
    def add_parent_relation(genealogy_id: int):
        require_genealogy_access(genealogy_id)
        parent_id = int(request.form["parent_id"])
        child_id = int(request.form["child_id"])
        relation_type = request.form["relation_type"]
        require_member(genealogy_id, parent_id)
        require_member(genealogy_id, child_id)
        try:
            execute(
                """
                INSERT INTO parent_child_relations (parent_id, child_id, relation_type)
                VALUES (?, ?, ?)
                """,
                (parent_id, child_id, relation_type),
            )
            sync_child_generation(child_id)
            flash("父母子女关系已添加。", "success")
        except sqlite3.IntegrityError as exc:
            flash(f"关系添加失败：{exc}", "error")
        return redirect(url_for("member_detail", genealogy_id=genealogy_id, member_id=child_id))

    @app.route("/genealogies/<int:genealogy_id>/relations/marriage", methods=("POST",))
    @login_required
    def add_marriage(genealogy_id: int):
        require_genealogy_access(genealogy_id)
        member1_id = int(request.form["member1_id"])
        member2_id = int(request.form["member2_id"])
        married_year = to_int_or_none(request.form.get("married_year"))
        require_member(genealogy_id, member1_id)
        require_member(genealogy_id, member2_id)
        left, right = sorted((member1_id, member2_id))
        try:
            execute(
                """
                INSERT INTO marriages (member1_id, member2_id, married_year)
                VALUES (?, ?, ?)
                """,
                (left, right, married_year),
            )
            flash("婚姻关系已添加。", "success")
        except sqlite3.IntegrityError as exc:
            flash(f"婚姻关系添加失败：{exc}", "error")
        return redirect(url_for("member_detail", genealogy_id=genealogy_id, member_id=member1_id))

    @app.route("/genealogies/<int:genealogy_id>/queries", methods=("GET", "POST"))
    @login_required
    def queries(genealogy_id: int):
        genealogy = require_genealogy_access(genealogy_id)
        members = query_all(
            "SELECT id, name, generation, birth_year FROM members WHERE genealogy_id = ? ORDER BY generation, id",
            (genealogy_id,),
        )
        result = None
        query_type = request.form.get("query_type", "ancestors")
        if request.method == "POST":
            if query_type == "ancestors":
                result = ancestors(int(request.form["member_id"]))
            elif query_type == "kinship":
                result = kinship_path(
                    genealogy_id,
                    int(request.form["member_a_id"]),
                    int(request.form["member_b_id"]),
                )
            elif query_type == "family":
                member_id = int(request.form["member_id"])
                result = {
                    "spouses": spouses_for(member_id),
                    "children": query_all(
                        """
                        SELECT c.*, r.relation_type
                        FROM parent_child_relations r
                        JOIN members c ON c.id = r.child_id
                        WHERE r.parent_id = ?
                        ORDER BY c.birth_year, c.id
                        """,
                        (member_id,),
                    ),
                }
        return render_template(
            "queries.html",
            genealogy=genealogy,
            members=members,
            query_type=query_type,
            result=result,
        )

    @app.route("/genealogies/<int:genealogy_id>/export")
    @login_required
    def export_branch(genealogy_id: int):
        genealogy = require_genealogy_access(genealogy_id)
        root_id = request.args.get("root_id", type=int)
        if root_id is None:
            flash("请提供 root_id。", "error")
            return redirect(url_for("genealogy_detail", genealogy_id=genealogy_id))
        require_member(genealogy_id, root_id)
        rows = descendants_flat(root_id)
        exports_dir = BASE_DIR / "exports"
        exports_dir.mkdir(exist_ok=True)
        file_path = exports_dir / f"genealogy_{genealogy_id}_branch_{root_id}.csv"
        with file_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["id", "name", "gender", "birth_year", "death_year", "generation", "depth"])
            for row in rows:
                writer.writerow(
                    [
                        row["id"],
                        row["name"],
                        row["gender"],
                        row["birth_year"],
                        row["death_year"],
                        row["generation"],
                        row["depth"],
                    ]
                )
        flash(f"分支已导出到 {file_path.relative_to(BASE_DIR)}。", "success")
        return redirect(url_for("genealogy_detail", genealogy_id=genealogy_id, root_id=root_id))

    return app


def register_cli(app: Flask) -> None:
    @app.cli.command("init-db")
    def init_db_command() -> None:
        init_db()
        print(f"数据库已初始化：{DATABASE}")

    @app.cli.command("seed-demo")
    def seed_demo_command() -> None:
        seed_demo()
        print("演示数据已写入。登录账号：admin / admin123")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(current_app_database())
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def current_app_database() -> str:
    return current_app.config["DATABASE"]


def init_db() -> None:
    db = sqlite3.connect(str(DATABASE))
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript((BASE_DIR / "schema.sql").read_text(encoding="utf-8"))
    db.commit()
    db.close()


def seed_demo() -> None:
    db = sqlite3.connect(str(DATABASE))
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript((BASE_DIR / "schema.sql").read_text(encoding="utf-8"))
    cur = db.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO users (username, password_hash) VALUES (?, ?)",
        ("admin", generate_password_hash("admin123")),
    )
    user_id = cur.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    cur.execute(
        """
        INSERT OR IGNORE INTO genealogies (name, surname, revision_time, creator_user_id)
        VALUES (?, ?, ?, ?)
        """,
        ("李氏示例族谱", "李", "2026-05-14", user_id),
    )
    genealogy_id = cur.execute("SELECT id FROM genealogies WHERE name = '李氏示例族谱'").fetchone()[0]
    count = cur.execute("SELECT COUNT(*) FROM members WHERE genealogy_id = ?", (genealogy_id,)).fetchone()[0]
    if count == 0:
        demo_members = [
            ("李元", "M", 1880, 1950, 1, "一世祖。"),
            ("王兰", "F", 1884, 1962, 1, "配偶。"),
            ("李承德", "M", 1910, 1980, 2, ""),
            ("李承芳", "F", 1914, 1990, 2, ""),
            ("张敏", "F", 1912, 1977, 2, ""),
            ("李家和", "M", 1940, 2018, 3, ""),
            ("李家宁", "F", 1945, None, 3, ""),
            ("赵晴", "F", 1944, None, 3, ""),
            ("李明", "M", 1970, None, 4, ""),
            ("李雅", "F", 1973, None, 4, ""),
            ("陈雪", "F", 1972, None, 4, ""),
            ("李一凡", "M", 2000, None, 5, ""),
            ("李一诺", "F", 2004, None, 5, ""),
        ]
        member_ids: dict[str, int] = {}
        for item in demo_members:
            cur.execute(
                """
                INSERT INTO members
                    (genealogy_id, name, gender, birth_year, death_year, generation, biography)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (genealogy_id, *item),
            )
            member_ids[item[0]] = cur.lastrowid
        parent_links = [
            ("李元", "李承德", "father"),
            ("王兰", "李承德", "mother"),
            ("李元", "李承芳", "father"),
            ("王兰", "李承芳", "mother"),
            ("李承德", "李家和", "father"),
            ("张敏", "李家和", "mother"),
            ("李承德", "李家宁", "father"),
            ("张敏", "李家宁", "mother"),
            ("李家和", "李明", "father"),
            ("赵晴", "李明", "mother"),
            ("李家和", "李雅", "father"),
            ("赵晴", "李雅", "mother"),
            ("李明", "李一凡", "father"),
            ("陈雪", "李一凡", "mother"),
            ("李明", "李一诺", "father"),
            ("陈雪", "李一诺", "mother"),
        ]
        for parent, child, relation_type in parent_links:
            cur.execute(
                """
                INSERT INTO parent_child_relations (parent_id, child_id, relation_type)
                VALUES (?, ?, ?)
                """,
                (member_ids[parent], member_ids[child], relation_type),
            )
        marriages = [("李元", "王兰", 1905), ("李承德", "张敏", 1936), ("李家和", "赵晴", 1966), ("李明", "陈雪", 1996)]
        for left, right, year in marriages:
            a, b = sorted((member_ids[left], member_ids[right]))
            cur.execute(
                "INSERT INTO marriages (member1_id, member2_id, married_year) VALUES (?, ?, ?)",
                (a, b, year),
            )
    db.commit()
    db.close()


def login_required(view):
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(**kwargs)

    wrapped_view.__name__ = view.__name__
    return wrapped_view


def query_all(sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    return get_db().execute(sql, params).fetchall()


def query_one(sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    return get_db().execute(sql, params).fetchone()


def query_value(sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = get_db().execute(sql, params).fetchone()
    return row[0] if row else None


def execute(sql: str, params: tuple[Any, ...] = ()) -> None:
    db = get_db()
    db.execute(sql, params)
    db.commit()


def accessible_genealogies() -> list[sqlite3.Row]:
    return query_all(
        """
        SELECT DISTINCT g.*, u.username AS creator_name
        FROM genealogies g
        JOIN users u ON u.id = g.creator_user_id
        LEFT JOIN genealogy_collaborators c ON c.genealogy_id = g.id
        WHERE g.creator_user_id = ? OR c.user_id = ?
        ORDER BY g.created_at DESC, g.id DESC
        """,
        (g.user["id"], g.user["id"]),
    )


def require_genealogy_access(genealogy_id: int, owner_only: bool = False) -> sqlite3.Row:
    if owner_only:
        genealogy = query_one(
            """
            SELECT g.*, u.username AS creator_name
            FROM genealogies g
            JOIN users u ON u.id = g.creator_user_id
            WHERE g.id = ? AND g.creator_user_id = ?
            """,
            (genealogy_id, g.user["id"]),
        )
    else:
        genealogy = query_one(
            """
            SELECT DISTINCT g.*, u.username AS creator_name
            FROM genealogies g
            JOIN users u ON u.id = g.creator_user_id
            LEFT JOIN genealogy_collaborators c ON c.genealogy_id = g.id
            WHERE g.id = ? AND (g.creator_user_id = ? OR c.user_id = ?)
            """,
            (genealogy_id, g.user["id"], g.user["id"]),
        )
    if genealogy is None:
        flash("没有权限访问该族谱。", "error")
        abort(403)
    return genealogy


def require_member(genealogy_id: int, member_id: int) -> sqlite3.Row:
    member = query_one("SELECT * FROM members WHERE id = ? AND genealogy_id = ?", (member_id, genealogy_id))
    if member is None:
        flash("成员不存在。", "error")
        abort(404)
    return member


def member_form_data() -> dict[str, Any]:
    name = request.form["name"].strip()
    if not name:
        raise ValueError("name is required")
    return {
        "name": name,
        "gender": request.form["gender"],
        "birth_year": to_int_or_none(request.form.get("birth_year")),
        "death_year": to_int_or_none(request.form.get("death_year")),
        "generation": max(1, int(request.form.get("generation") or 1)),
        "biography": request.form.get("biography", "").strip(),
    }


def to_int_or_none(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    return int(value)


def spouses_for(member_id: int) -> list[sqlite3.Row]:
    return query_all(
        """
        SELECT m.*, s.married_year, s.status
        FROM marriages s
        JOIN members m ON m.id = CASE
            WHEN s.member1_id = ? THEN s.member2_id
            ELSE s.member1_id
        END
        WHERE s.member1_id = ? OR s.member2_id = ?
        ORDER BY s.married_year, m.id
        """,
        (member_id, member_id, member_id),
    )


def sync_child_generation(child_id: int) -> None:
    row = query_one(
        """
        SELECT MAX(p.generation) + 1 AS expected_generation
        FROM parent_child_relations r
        JOIN members p ON p.id = r.parent_id
        WHERE r.child_id = ?
        """,
        (child_id,),
    )
    if row and row["expected_generation"]:
        execute("UPDATE members SET generation = ? WHERE id = ?", (row["expected_generation"], child_id))


def descendants_flat(root_id: int) -> list[sqlite3.Row]:
    return query_all(
        """
        WITH RECURSIVE descendants(id, name, gender, birth_year, death_year, generation, depth) AS (
            SELECT id, name, gender, birth_year, death_year, generation, 0
            FROM members
            WHERE id = ?
            UNION ALL
            SELECT c.id, c.name, c.gender, c.birth_year, c.death_year, c.generation, d.depth + 1
            FROM descendants d
            JOIN parent_child_relations r ON r.parent_id = d.id
            JOIN members c ON c.id = r.child_id
        )
        SELECT DISTINCT * FROM descendants ORDER BY depth, birth_year, id
        """,
        (root_id,),
    )


def descendants_tree(genealogy_id: int, root_id: int | None) -> list[dict[str, Any]]:
    if root_id is None:
        root = query_one(
            """
            SELECT * FROM members
            WHERE genealogy_id = ?
              AND id NOT IN (SELECT child_id FROM parent_child_relations)
            ORDER BY birth_year, id
            LIMIT 1
            """,
            (genealogy_id,),
        )
        root_id = root["id"] if root else None
    if root_id is None:
        return []
    rows = descendants_flat(root_id)
    return [{"member": row, "indent": row["depth"] * 24} for row in rows]


def ancestors(member_id: int) -> list[sqlite3.Row]:
    return query_all(
        """
        WITH RECURSIVE ancestors(id, name, gender, birth_year, death_year, generation, depth, relation_type) AS (
            SELECT p.id, p.name, p.gender, p.birth_year, p.death_year, p.generation, 1, r.relation_type
            FROM parent_child_relations r
            JOIN members p ON p.id = r.parent_id
            WHERE r.child_id = ?
            UNION ALL
            SELECT gp.id, gp.name, gp.gender, gp.birth_year, gp.death_year, gp.generation,
                   a.depth + 1, r.relation_type
            FROM ancestors a
            JOIN parent_child_relations r ON r.child_id = a.id
            JOIN members gp ON gp.id = r.parent_id
        )
        SELECT DISTINCT * FROM ancestors ORDER BY depth, relation_type, birth_year, id
        """,
        (member_id,),
    )


def kinship_path(genealogy_id: int, member_a_id: int, member_b_id: int) -> dict[str, Any] | None:
    require_member(genealogy_id, member_a_id)
    require_member(genealogy_id, member_b_id)
    path_row = query_one(
        """
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
            SELECT ?, printf('%d', ?), '', 0
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
        SELECT s.depth, s.labels, s.path
        FROM search s
        WHERE s.id = ?
        ORDER BY s.depth
        LIMIT 1
        """,
        (member_a_id, member_a_id, member_b_id),
    )
    if path_row is None:
        return None
    ids = [int(item) for item in path_row["path"].split(",")]
    placeholders = ",".join("?" for _ in ids)
    rows = query_all(
        f"SELECT * FROM members WHERE id IN ({placeholders})",
        tuple(ids),
    )
    by_id = {row["id"]: row for row in rows}
    return {
        "depth": path_row["depth"],
        "labels": path_row["labels"],
        "members": [by_id[item] for item in ids],
    }


app = create_app()


if __name__ == "__main__":
    if not DATABASE.exists():
        init_db()
    app.run(debug=True)
