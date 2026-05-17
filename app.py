from __future__ import annotations

import csv
import os
import re
import sqlite3
from pathlib import Path
from collections import defaultdict, deque
from typing import Any

TREE_PREVIEW_LIMIT = 200

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


def display_name(name: str | None) -> str:
    if not name:
        return ""
    return re.sub(r"\d+$", "", str(name))


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-genealogy-secret")
    app.config["DATABASE"] = os.environ.get("DATABASE", str(DATABASE))
    app.jinja_env.filters["display_name"] = display_name
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
        total = query_value("SELECT COUNT(*) FROM members WHERE genealogy_id = ?", (genealogy_id,))
        page = max(1, request.args.get("page", default=1, type=int))
        per_page = min(max(request.args.get("per_page", default=50, type=int), 20), 100)
        member_total = count_members(genealogy_id, keyword)
        total_pages = max(1, (member_total + per_page - 1) // per_page)
        page = min(page, total_pages)
        members = search_members(genealogy_id, keyword, limit=per_page, offset=(page - 1) * per_page)
        tree_limit = min(max(request.args.get("tree_limit", default=TREE_PREVIEW_LIMIT, type=int), 50), 1000)
        tree, tree_truncated, tree_count = descendants_tree(
            genealogy_id,
            request.args.get("root_id", type=int),
            limit=tree_limit,
        )
        return render_template(
            "genealogy.html",
            genealogy=genealogy,
            members=members,
            keyword=keyword,
            total=total,
            member_total=member_total,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
            tree=tree,
            tree_truncated=tree_truncated,
            tree_limit=tree_limit,
            tree_count=tree_count,
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
        query_samples = query_all(
            """
            SELECT id, name, generation, birth_year
            FROM members
            WHERE genealogy_id = ?
            ORDER BY generation, birth_year, id
            LIMIT 12
            """,
            (genealogy_id,),
        )
        result = None
        query_type = request.form.get("query_type", "ancestors")
        if request.method == "POST":
            try:
                if query_type == "ancestors":
                    member_id = form_member_id("member_id")
                    member = require_member(genealogy_id, member_id)
                    ancestor_rows, truncated = ancestors(member_id)
                    result = {"member": member, "ancestors": ancestor_rows, "truncated": truncated}
                elif query_type == "name_lookup":
                    name = request.form.get("name", "").strip()
                    if not name:
                        raise ValueError("请填写姓名")
                    result = {"name": name, "rows": lookup_members_by_name(genealogy_id, name)}
                elif query_type == "kinship":
                    include_marriage = request.form.get("include_marriage") == "1"
                    result = kinship_path(
                        genealogy_id,
                        form_member_id("member_a_id"),
                        form_member_id("member_b_id"),
                        include_marriage=include_marriage,
                        max_depth=min(max(int(request.form.get("max_depth") or 20), 1), 30),
                    )
                elif query_type == "family":
                    member_id = form_member_id("member_id")
                    member = require_member(genealogy_id, member_id)
                    result = family_snapshot(member_id)
                    result["member"] = member
                elif query_type == "descendants":
                    member_id = form_member_id("member_id")
                    member = require_member(genealogy_id, member_id)
                    limit = min(max(int(request.form.get("limit") or 200), 1), 1000)
                    rows, truncated = descendants_flat(member_id, limit=limit)
                    result = {"member": member, "rows": rows, "truncated": truncated, "limit": limit}
                elif query_type == "great_grandchildren":
                    member_id = form_member_id("member_id")
                    member = require_member(genealogy_id, member_id)
                    result = {"member": member, "rows": great_grandchildren(member_id)}
                elif query_type == "generation_lifespan":
                    result = generation_lifespan_stats(genealogy_id)
                elif query_type == "unmarried_males":
                    age = min(max(int(request.form.get("age") or 50), 1), 150)
                    result = {"age": age, "rows": unmarried_males(genealogy_id, age)}
                elif query_type == "early_birth":
                    result = early_birth_members(genealogy_id)
                elif query_type == "common_ancestor":
                    result = closest_common_ancestor(
                        genealogy_id,
                        form_member_id("member_a_id"),
                        form_member_id("member_b_id"),
                    )
                elif query_type == "generation_profile":
                    result = generation_profile(genealogy_id)
            except (ValueError, sqlite3.Error) as exc:
                flash(f"查询失败：{exc}", "error")
        return render_template(
            "queries.html",
            genealogy=genealogy,
            query_samples=query_samples,
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
        rows, _ = descendants_flat(root_id)
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
        g.db.execute("PRAGMA busy_timeout = 5000")
        g.db.execute("PRAGMA journal_mode = WAL")
        g.db.execute("PRAGMA synchronous = NORMAL")
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
    with db:
        db.execute(sql, params)


def count_members(genealogy_id: int, keyword: str = "") -> int:
    if keyword:
        return int(
            query_value(
                "SELECT COUNT(*) FROM members WHERE genealogy_id = ? AND name LIKE ?",
                (genealogy_id, f"%{keyword}%"),
            )
            or 0
        )
    return int(query_value("SELECT COUNT(*) FROM members WHERE genealogy_id = ?", (genealogy_id,)) or 0)


def search_members(genealogy_id: int, keyword: str, limit: int = 200, offset: int = 0) -> list[sqlite3.Row]:
    if not keyword:
        return query_all(
            """
            SELECT * FROM members
            WHERE genealogy_id = ?
            ORDER BY generation, birth_year, id
            LIMIT ? OFFSET ?
            """,
            (genealogy_id, limit, offset),
        )

    has_fts = query_one(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'members_fts'"
    )
    if has_fts:
        fts_query = " ".join(part.replace('"', '""') + "*" for part in keyword.split())
        if fts_query:
            rows = query_all(
                """
                SELECT m.*
                FROM members_fts
                JOIN members m ON m.id = members_fts.rowid
                WHERE members_fts.name MATCH ? AND m.genealogy_id = ?
                ORDER BY bm25(members_fts), m.generation, m.birth_year, m.id
                LIMIT ? OFFSET ?
                """,
                (fts_query, genealogy_id, limit, offset),
            )
            if rows:
                return rows

    return query_all(
        """
        SELECT * FROM members
        WHERE genealogy_id = ? AND name LIKE ?
        ORDER BY generation, birth_year, id
        LIMIT ? OFFSET ?
        """,
        (genealogy_id, f"%{keyword}%", limit, offset),
    )


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


def form_member_id(field_name: str) -> int:
    value = request.form.get(field_name, "").strip()
    if not value:
        raise ValueError("请填写成员 ID")
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


def family_snapshot(member_id: int) -> dict[str, Any]:
    return {
        "parents": query_all(
            """
            SELECT p.*, r.relation_type
            FROM parent_child_relations r
            JOIN members p ON p.id = r.parent_id
            WHERE r.child_id = ?
            ORDER BY r.relation_type, p.birth_year, p.id
            """,
            (member_id,),
        ),
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


def lookup_members_by_name(genealogy_id: int, name: str) -> list[sqlite3.Row]:
    return search_members(genealogy_id, name, limit=200, offset=0)


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


def descendants_flat(root_id: int, limit: int | None = None) -> tuple[list[dict[str, Any]], bool]:
    """DFS 遍历后代，保持“祖先 -> 一个子树完整展开 -> 下一个子树”的族谱顺序。"""
    root = query_one(
        "SELECT id, genealogy_id FROM members WHERE id = ?",
        (root_id,),
    )
    if root is None:
        return [], False

    genealogy_id = root["genealogy_id"]
    edges = query_all(
        """
        SELECT r.parent_id, r.child_id
        FROM parent_child_relations r
        JOIN members p ON p.id = r.parent_id
        WHERE p.genealogy_id = ?
        """,
        (genealogy_id,),
    )
    children_map: dict[int, list[int]] = defaultdict(list)
    for edge in edges:
        children_map[edge["parent_id"]].append(edge["child_id"])

    members_by_id = {
        row["id"]: row
        for row in query_all(
            """
            SELECT id, name, gender, birth_year, death_year, generation
            FROM members
            WHERE genealogy_id = ?
            """,
            (genealogy_id,),
        )
    }

    def members_birth_sort_key(member_id: int) -> int:
        member = members_by_id.get(member_id)
        return member["birth_year"] if member and member["birth_year"] is not None else 99999

    for children in children_map.values():
        children.sort(
            key=lambda child_id: (
                members_birth_sort_key(child_id),
                child_id,
            )
        )

    result: list[dict[str, Any]] = []
    visited = {root_id}
    truncated = False

    def visit(member_id: int, depth: int) -> None:
        nonlocal truncated
        if truncated:
            return
        if limit is not None and len(result) >= limit:
            truncated = True
            return
        member = members_by_id.get(member_id)
        if member is None:
            return
        row = dict(member)
        row["depth"] = depth
        result.append(row)
        for child_id in children_map.get(member_id, []):
            if child_id in visited:
                continue
            visited.add(child_id)
            visit(child_id, depth + 1)

    visit(root_id, 0)
    return result, truncated


def descendants_tree(genealogy_id: int, root_id: int | None, limit: int = TREE_PREVIEW_LIMIT) -> tuple[dict[str, Any] | None, bool, int]:
    if root_id is None:
        root = query_one(
            """
            SELECT m.*
            FROM members m
            LEFT JOIN parent_child_relations r ON r.child_id = m.id
            WHERE m.genealogy_id = ? AND r.child_id IS NULL
            ORDER BY m.birth_year, m.id
            LIMIT 1
            """,
            (genealogy_id,),
        )
        root_id = root["id"] if root else None
    if root_id is None:
        return None, False, 0

    root = require_member(genealogy_id, root_id)
    members = query_all(
        """
        SELECT id, name, gender, birth_year, death_year, generation
        FROM members
        WHERE genealogy_id = ?
        """,
        (genealogy_id,),
    )
    members_by_id = {row["id"]: row for row in members}
    children_map: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in query_all(
        """
        SELECT r.parent_id, r.child_id, r.relation_type, c.birth_year, c.id
        FROM parent_child_relations r
        JOIN members p ON p.id = r.parent_id
        JOIN members c ON c.id = r.child_id
        WHERE p.genealogy_id = ?
        ORDER BY c.birth_year, c.id
        """,
        (genealogy_id,),
    ):
        children_map[row["parent_id"]].append(dict(row))

    spouses_map: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in query_all(
        """
        SELECT s.member1_id, s.member2_id, s.married_year, s.status
        FROM marriages s
        JOIN members m1 ON m1.id = s.member1_id
        JOIN members m2 ON m2.id = s.member2_id
        WHERE m1.genealogy_id = ? AND m2.genealogy_id = ?
        ORDER BY s.married_year, s.id
        """,
        (genealogy_id, genealogy_id),
    ):
        left_id = row["member1_id"]
        right_id = row["member2_id"]
        left = members_by_id.get(left_id)
        right = members_by_id.get(right_id)
        if left and right:
            spouses_map[left_id].append(
                {
                    "id": right["id"],
                    "name": right["name"],
                    "gender": right["gender"],
                    "birth_year": right["birth_year"],
                    "death_year": right["death_year"],
                    "generation": right["generation"],
                    "married_year": row["married_year"],
                    "status": row["status"],
                }
            )
            spouses_map[right_id].append(
                {
                    "id": left["id"],
                    "name": left["name"],
                    "gender": left["gender"],
                    "birth_year": left["birth_year"],
                    "death_year": left["death_year"],
                    "generation": left["generation"],
                    "married_year": row["married_year"],
                    "status": row["status"],
                }
            )

    visited = set()
    count = 0
    truncated = False

    def build(member_id: int, depth: int, relation_type: str | None = None) -> dict[str, Any] | None:
        nonlocal count, truncated
        if truncated or member_id in visited:
            return None
        if count >= limit:
            truncated = True
            return None
        member = members_by_id.get(member_id)
        if member is None:
            return None

        visited.add(member_id)
        count += 1
        children = []
        for edge in children_map.get(member_id, []):
            child = build(edge["child_id"], depth + 1, edge["relation_type"])
            if child is not None:
                children.append(child)

        return {
            "member": member,
            "spouses": spouses_map.get(member_id, []),
            "children": children,
            "depth": depth,
            "relation_label": relation_label(relation_type),
        }

    return build(root["id"], 0), truncated, count


def relation_label(relation_type: str | None) -> str:
    if relation_type == "father":
        return "父系子女"
    if relation_type == "mother":
        return "母系子女"
    return "根节点"


def ancestors(member_id: int, limit: int = 500) -> tuple[list[dict[str, Any]], bool]:
    member = query_one("SELECT id, genealogy_id FROM members WHERE id = ?", (member_id,))
    if member is None:
        return [], False

    edges = query_all(
        """
        SELECT r.child_id, r.parent_id, r.relation_type,
               p.name, p.gender, p.birth_year, p.death_year, p.generation
        FROM parent_child_relations r
        JOIN members p ON p.id = r.parent_id
        WHERE p.genealogy_id = ?
        ORDER BY p.birth_year, p.id
        """,
        (member["genealogy_id"],),
    )
    parents_by_child: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for edge in edges:
        parents_by_child[edge["child_id"]].append(edge)

    result: list[dict[str, Any]] = []
    visited = {member_id}
    queue = deque([(member_id, 0)])
    truncated = False
    while queue:
        child_id, depth = queue.popleft()
        for edge in parents_by_child.get(child_id, []):
            parent_id = edge["parent_id"]
            if parent_id in visited:
                continue
            if len(result) >= limit:
                truncated = True
                return result, truncated
            visited.add(parent_id)
            result.append(
                {
                    "id": parent_id,
                    "name": edge["name"],
                    "gender": edge["gender"],
                    "birth_year": edge["birth_year"],
                    "death_year": edge["death_year"],
                    "generation": edge["generation"],
                    "depth": depth + 1,
                    "relation_type": edge["relation_type"],
                }
            )
            queue.append((parent_id, depth + 1))

    result.sort(key=lambda row: (row["depth"], row["relation_type"], row["birth_year"] or 0, row["id"]))
    return result, truncated


def great_grandchildren(member_id: int) -> list[sqlite3.Row]:
    return query_all(
        """
        SELECT DISTINCT great_grandchild.*
        FROM parent_child_relations r1
        JOIN parent_child_relations r2 ON r2.parent_id = r1.child_id
        JOIN parent_child_relations r3 ON r3.parent_id = r2.child_id
        JOIN members great_grandchild ON great_grandchild.id = r3.child_id
        WHERE r1.parent_id = ?
        ORDER BY great_grandchild.birth_year, great_grandchild.id
        LIMIT 500
        """,
        (member_id,),
    )


def generation_lifespan_stats(genealogy_id: int) -> dict[str, Any]:
    rows = query_all(
        """
        SELECT generation,
               COUNT(*) AS member_count,
               AVG(COALESCE(death_year, CAST(strftime('%Y', 'now') AS INTEGER)) - birth_year) AS avg_lifespan
        FROM members
        WHERE genealogy_id = ?
          AND birth_year IS NOT NULL
        GROUP BY generation
        ORDER BY avg_lifespan DESC, generation
        LIMIT 20
        """,
        (genealogy_id,),
    )
    return {"rows": rows, "best": rows[0] if rows else None}


def unmarried_males(genealogy_id: int, age: int) -> list[sqlite3.Row]:
    return query_all(
        """
        SELECT m.*
        FROM members m
        WHERE m.genealogy_id = ?
          AND m.gender = 'M'
          AND m.birth_year IS NOT NULL
          AND CAST(strftime('%Y', 'now') AS INTEGER) - m.birth_year > ?
          AND NOT EXISTS (
              SELECT 1
              FROM marriages s
              WHERE s.member1_id = m.id OR s.member2_id = m.id
          )
        ORDER BY m.birth_year, m.id
        LIMIT 200
        """,
        (genealogy_id, age),
    )


def early_birth_members(genealogy_id: int) -> list[sqlite3.Row]:
    return query_all(
        """
        WITH generation_avg AS (
            SELECT genealogy_id, generation, AVG(birth_year) AS avg_birth_year
            FROM members
            WHERE genealogy_id = ?
              AND birth_year IS NOT NULL
            GROUP BY genealogy_id, generation
        )
        SELECT m.*, g.avg_birth_year
        FROM members m
        JOIN generation_avg g
          ON g.genealogy_id = m.genealogy_id
         AND g.generation = m.generation
        WHERE m.genealogy_id = ?
          AND m.birth_year IS NOT NULL
          AND m.birth_year < g.avg_birth_year
        ORDER BY m.generation, m.birth_year, m.id
        LIMIT 200
        """,
        (genealogy_id, genealogy_id),
    )


def generation_profile(genealogy_id: int) -> list[sqlite3.Row]:
    return query_all(
        """
        SELECT generation,
               COUNT(*) AS member_count,
               SUM(CASE WHEN gender = 'M' THEN 1 ELSE 0 END) AS male_count,
               SUM(CASE WHEN gender = 'F' THEN 1 ELSE 0 END) AS female_count,
               MIN(birth_year) AS earliest_birth,
               MAX(birth_year) AS latest_birth
        FROM members
        WHERE genealogy_id = ?
        GROUP BY generation
        ORDER BY generation
        LIMIT 80
        """,
        (genealogy_id,),
    )


def ancestor_depths(member_id: int) -> dict[int, int]:
    rows = query_all(
        """
        WITH RECURSIVE up(id, depth) AS (
            SELECT parent_id, 1
            FROM parent_child_relations
            WHERE child_id = ?
            UNION ALL
            SELECT r.parent_id, up.depth + 1
            FROM up
            JOIN parent_child_relations r ON r.child_id = up.id
            WHERE up.depth < 40
        )
        SELECT id, MIN(depth) AS depth
        FROM up
        GROUP BY id
        """,
        (member_id,),
    )
    return {row["id"]: row["depth"] for row in rows}


def closest_common_ancestor(genealogy_id: int, member_a_id: int, member_b_id: int) -> dict[str, Any]:
    member_a = require_member(genealogy_id, member_a_id)
    member_b = require_member(genealogy_id, member_b_id)
    ancestors_a = ancestor_depths(member_a_id)
    ancestors_b = ancestor_depths(member_b_id)
    common_ids = set(ancestors_a) & set(ancestors_b)
    if not common_ids:
        return {"member_a": member_a, "member_b": member_b, "ancestor": None}
    ancestor_id = min(common_ids, key=lambda item: (ancestors_a[item] + ancestors_b[item], max(ancestors_a[item], ancestors_b[item]), item))
    return {
        "member_a": member_a,
        "member_b": member_b,
        "ancestor": query_one("SELECT * FROM members WHERE id = ?", (ancestor_id,)),
        "depth_a": ancestors_a[ancestor_id],
        "depth_b": ancestors_b[ancestor_id],
    }


def kinship_path(
    genealogy_id: int,
    member_a_id: int,
    member_b_id: int,
    include_marriage: bool = False,
    max_depth: int = 20,
) -> dict[str, Any] | None:
    require_member(genealogy_id, member_a_id)
    require_member(genealogy_id, member_b_id)
    mode_label = "血缘关系" if not include_marriage else "血缘及婚姻关系"
    if member_a_id == member_b_id:
        member = require_member(genealogy_id, member_a_id)
        return {
            "depth": 0,
            "labels": "同一成员",
            "members": [member],
            "visited_count": 1,
            "include_marriage": include_marriage,
            "mode_label": mode_label,
        }

    graph: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for row in query_all(
        """
        SELECT r.parent_id, r.child_id, r.relation_type
        FROM parent_child_relations r
        JOIN members p ON p.id = r.parent_id
        WHERE p.genealogy_id = ?
        """,
        (genealogy_id,),
    ):
        down_label = "父亲 -> 子女" if row["relation_type"] == "father" else "母亲 -> 子女"
        up_label = "子女 -> 父亲" if row["relation_type"] == "father" else "子女 -> 母亲"
        graph[row["parent_id"]].append((row["child_id"], down_label))
        graph[row["child_id"]].append((row["parent_id"], up_label))
    if include_marriage:
        for row in query_all(
            """
            SELECT s.member1_id, s.member2_id
            FROM marriages s
            JOIN members m1 ON m1.id = s.member1_id
            WHERE m1.genealogy_id = ?
            """,
            (genealogy_id,),
        ):
            graph[row["member1_id"]].append((row["member2_id"], "配偶"))
            graph[row["member2_id"]].append((row["member1_id"], "配偶"))

    queue = deque([(member_a_id, [member_a_id], [])])
    visited = {member_a_id}
    while queue:
        current_id, path, labels = queue.popleft()
        if len(labels) >= max_depth:
            continue
        for next_id, label in graph.get(current_id, []):
            if next_id in visited:
                continue
            next_path = path + [next_id]
            next_labels = labels + [label]
            if next_id == member_b_id:
                rows = query_all(
                    f"SELECT * FROM members WHERE id IN ({','.join('?' for _ in next_path)})",
                    tuple(next_path),
                )
                by_id = {row["id"]: row for row in rows}
                return {
                    "depth": len(next_labels),
                    "labels": " -> ".join(next_labels),
                    "members": [by_id[item] for item in next_path],
                    "visited_count": len(visited),
                    "include_marriage": include_marriage,
                    "mode_label": mode_label,
                }
            visited.add(next_id)
            queue.append((next_id, next_path, next_labels))

    if len(visited) > 0:
        return {
            "depth": None,
            "labels": "",
            "members": [],
            "visited_count": len(visited),
            "max_depth": max_depth,
            "include_marriage": include_marriage,
            "mode_label": mode_label,
        }
    else:
        return None


app = create_app()


if __name__ == "__main__":
    if not DATABASE.exists():
        init_db()
    app.run(debug=True)
