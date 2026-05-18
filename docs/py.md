# Python 代码讲解

本文分别讲解项目中的两个核心 Python 文件：

- `app.py`：Flask Web 应用，负责登录注册、族谱管理、成员管理、查询中心、树形预览、导出等功能。
- `scripts/generate_data.py`：十万级模拟数据生成脚本，负责生成用户、族谱、成员、亲子关系和婚姻关系 CSV。

## 一、app.py 讲解

### 1. 文件整体职责

`app.py` 是整个系统的主程序。它完成三类工作：

1. 创建 Flask 应用并注册路由。
2. 连接 SQLite 数据库并执行查询。
3. 实现族谱业务逻辑，包括权限控制、成员 CRUD、树形查询、祖先查询、亲缘链路查询和统计分析。

项目启动时，文件末尾会执行：

```python
app = create_app()
```

也就是说，Flask 命令：

```bash
flask --app app run
```

会加载这个 `app` 对象。

### 2. 全局配置

文件开头定义了几个重要常量：

```python
TREE_PREVIEW_LIMIT = 200
DASHBOARD_SORTS = {...}
BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "genealogy.db"
```

含义如下：

- `TREE_PREVIEW_LIMIT`：树形预览默认最多渲染 200 个节点，避免大族谱一次性渲染过多导致页面卡顿。
- `DASHBOARD_SORTS`：Dashboard 支持的排序方式，例如人数降序、人数升序、最近创建优先。
- `BASE_DIR`：项目根目录。
- `DATABASE`：默认 SQLite 数据库文件，即 `genealogy.db`。

### 3. display_name()

```python
def display_name(name: str | None) -> str:
    if not name:
        return ""
    return re.sub(r"\d+$", "", str(name))
```

这个函数用于展示姓名。

模拟数据中的姓名可能是：

```text
李明12
陈芳8
```

末尾数字表示世代。页面展示时不希望名字后面带数字，所以使用正则去掉结尾数字。

该函数注册为 Jinja 过滤器：

```python
app.jinja_env.filters["display_name"] = display_name
```

模板中可以这样用：

```jinja2
{{ member.name|display_name }}
```

### 4. create_app()

`create_app()` 是 Flask 应用工厂函数，负责创建应用对象并注册全部路由。

主要逻辑包括：

```python
app = Flask(__name__)
app.config["SECRET_KEY"] = ...
app.config["DATABASE"] = ...
register_cli(app)
```

其中：

- `SECRET_KEY` 用于 Session 加密。
- `DATABASE` 指定 SQLite 文件路径。
- `register_cli(app)` 注册命令行命令，例如 `init-db` 和 `seed-demo`。

### 5. 请求前后处理

#### before_request：加载当前登录用户

```python
@app.before_request
def load_logged_in_user():
    user_id = session.get("user_id")
    ...
```

每次请求到来时，系统都会从 `session` 中取出 `user_id`。

如果用户已登录，就查询数据库，把用户信息放入：

```python
g.user
```

后续路由可以通过 `g.user` 判断当前用户是谁。

#### teardown_appcontext：关闭数据库连接

```python
@app.teardown_appcontext
def close_db(...):
    db = g.pop("db", None)
    if db is not None:
        db.close()
```

每次请求结束时关闭数据库连接，避免连接泄漏。

### 6. 登录注册功能

#### 注册

路由：

```python
@app.route("/register", methods=("GET", "POST"))
```

注册时会：

1. 获取用户名和密码。
2. 检查用户名是否为空。
3. 检查用户名是否已存在。
4. 使用 `generate_password_hash()` 保存密码 hash。

核心 SQL：

```sql
INSERT INTO users (username, password_hash) VALUES (?, ?)
```

#### 登录

路由：

```python
@app.route("/login", methods=("GET", "POST"))
```

登录时会：

1. 根据用户名查询用户。
2. 使用 `check_password_hash()` 校验密码。
3. 校验成功后写入 Session：

```python
session["user_id"] = user["id"]
```

后续请求就能识别当前用户。

#### 退出

```python
@app.route("/logout")
def logout():
    session.clear()
```

清空 Session 即退出登录。

### 7. Dashboard 功能

路由：

```python
@app.route("/dashboard")
```

Dashboard 展示族谱列表和统计信息，包括：

- 总人数
- 男性比例
- 女性比例
- 创建者
- 修谱时间

排序参数来自 URL：

```python
sort = request.args.get("sort", "members_desc")
```

支持：

- `members_desc`：人数从多到少
- `members_asc`：人数从少到多
- `created_desc`：最近创建优先

数据由 `dashboard_stats(sort)` 查询。

### 8. dashboard_stats()

```python
def dashboard_stats(sort: str) -> list[dict[str, Any]]:
```

这个函数使用一次 SQL 聚合查询得到所有族谱的统计数据：

```sql
COUNT(m.id) AS total
SUM(CASE WHEN m.gender = 'M' THEN 1 ELSE 0 END) AS male
SUM(CASE WHEN m.gender = 'F' THEN 1 ELSE 0 END) AS female
```

它比“每个族谱单独查三次”效率更高。

权限逻辑：

- 如果当前用户是 `admin`，可以看到全部族谱。
- 普通用户只能看到自己创建的或者受邀参与的族谱。

### 9. 族谱管理

#### 创建族谱

路由：

```python
@app.route("/genealogies/create", methods=("POST",))
```

用户提交：

- 谱名
- 姓氏
- 修谱时间

系统插入 `genealogies` 表：

```sql
INSERT INTO genealogies (name, surname, revision_time, creator_user_id)
VALUES (?, ?, ?, ?)
```

#### 查看族谱详情

路由：

```python
@app.route("/genealogies/<int:genealogy_id>")
```

族谱详情页包含：

- 成员列表
- 姓名模糊查找
- 分页
- 添加成员表单
- 邀请协作者
- 树形预览
- 分支导出入口

进入详情页前会调用：

```python
require_genealogy_access(genealogy_id)
```

用于检查当前用户是否有权限访问。

### 10. 权限控制

#### login_required()

```python
def login_required(view):
```

这是一个装饰器。加了 `@login_required` 的路由必须登录才能访问。

如果 `g.user is None`，就跳转到登录页。

#### require_genealogy_access()

```python
def require_genealogy_access(genealogy_id: int, owner_only: bool = False)
```

作用是检查当前用户是否能访问某个族谱。

规则：

- `admin` 可以访问全部族谱。
- 族谱创建者可以访问。
- 被邀请到 `genealogy_collaborators` 的用户可以访问。
- `owner_only=True` 时，必须是创建者才允许，例如邀请协作者。

#### require_member()

```python
def require_member(genealogy_id: int, member_id: int)
```

检查成员是否存在，并且是否属于当前族谱。

这样可以防止用户通过 URL 访问其他族谱的成员。

### 11. 成员管理

#### 添加成员

路由：

```python
@app.route("/genealogies/<int:genealogy_id>/members/create", methods=("POST",))
```

数据通过 `member_form_data()` 统一处理，包括：

- 姓名
- 性别
- 出生年
- 卒年
- 世代
- 生平简介

然后插入 `members` 表。

#### 查看和修改成员

路由：

```python
@app.route("/genealogies/<int:genealogy_id>/members/<int:member_id>", methods=("GET", "POST"))
```

GET 时展示成员详情，包括：

- 基本信息
- 父母
- 子女
- 配偶
- 添加亲子关系表单
- 添加婚姻关系表单

POST 时更新成员基本信息。

#### 删除成员

路由：

```python
@app.route("/genealogies/<int:genealogy_id>/members/<int:member_id>/delete", methods=("POST",))
```

删除成员时，数据库外键设置了 `ON DELETE CASCADE`，相关亲子关系和婚姻关系也会自动删除。

### 12. 亲子关系和婚姻关系

#### 添加父母子女关系

路由：

```python
@app.route("/genealogies/<int:genealogy_id>/relations/parent", methods=("POST",))
```

用户选择父/母、子女和关系类型：

```text
father 或 mother
```

系统插入 `parent_child_relations` 表。

插入后调用：

```python
sync_child_generation(child_id)
```

自动把子女世代调整为父母最大世代 + 1。

数据库触发器还会保证：

- 父母和子女属于同一族谱。
- 父母出生年早于子女。
- father 必须是男性或未知性别。
- mother 必须是女性或未知性别。
- 不能形成亲子环。

#### 添加婚姻关系

路由：

```python
@app.route("/genealogies/<int:genealogy_id>/relations/marriage", methods=("POST",))
```

婚姻关系用：

```python
left, right = sorted((member1_id, member2_id))
```

保证 `member1_id < member2_id`，避免同一对夫妻出现两个方向的重复记录。

### 13. 查询中心

路由：

```python
@app.route("/genealogies/<int:genealogy_id>/queries", methods=("GET", "POST"))
```

查询中心根据表单里的：

```python
query_type
```

判断执行哪一种查询。

主要查询类型包括：

| query_type | 功能 |
| --- | --- |
| `name_lookup` | 根据姓名模糊查 ID |
| `ancestors` | 查询历代祖先 |
| `kinship` | 查询两个人之间的亲缘通路 |
| `family` | 查询父母、配偶、子女 |
| `descendants` | 查询直系后代 |
| `great_grandchildren` | 查询曾孙 |
| `generation_lifespan` | 平均寿命最长世代 |
| `unmarried_males` | 高龄无配偶男性 |
| `early_birth` | 早于同代平均出生年的成员 |
| `common_ancestor` | 最近共同祖先 |
| `generation_profile` | 世代画像 |

### 14. 姓名模糊查询

```python
def search_members(genealogy_id: int, keyword: str, limit: int = 200, offset: int = 0)
```

如果没有关键词，就按世代、出生年、ID 展示成员。

如果有关键词，优先使用 SQLite FTS5：

```sql
members_fts.name MATCH ?
```

如果 FTS 不可用或没有结果，就回退到：

```sql
name LIKE '%关键词%'
```

这样既支持快速全文检索，也保证功能可用。

### 15. 家庭关系查询

```python
def family_snapshot(member_id: int)
```

一次返回三组关系：

- 父母：从 `parent_child_relations` 中找 `child_id = 当前成员`
- 配偶：从 `marriages` 中找当前成员所在的婚姻记录
- 子女：从 `parent_child_relations` 中找 `parent_id = 当前成员`

### 16. 直系后代查询

```python
def descendants_flat(root_id: int, limit: int | None = None)
```

这个函数用于查询某个成员的全部直系后代。

核心思路：

1. 一次性读取当前族谱所有亲子边。
2. 构建 `parent_id -> children` 映射。
3. 从根成员开始 DFS 深度优先遍历。
4. 使用 `visited` 防止重复访问。
5. 返回列表，每行带 `depth` 表示距离根节点几代。

DFS 顺序更适合族谱阅读：先完整展开一个子女分支，再进入下一个分支。

### 17. 树形预览

```python
def descendants_tree(genealogy_id: int, root_id: int | None, limit: int = TREE_PREVIEW_LIMIT)
```

族谱详情页的树形预览使用这个函数。

它会一次性读取：

- 当前族谱的所有成员
- 当前族谱的所有亲子关系
- 当前族谱的所有婚姻关系

然后建立：

```python
children_map
spouses_map
```

最后递归构造嵌套树结构，模板中用 Jinja 宏渲染成树。

这样可以避免每展开一个节点都查询数据库，减少 N+1 查询问题。

### 18. 祖先查询

```python
def ancestors(member_id: int, limit: int = 500)
```

作用是查询某个成员所有父辈以上祖先。

实现方式：

1. 查询当前族谱所有 `child_id -> parent_id` 边。
2. 建立 `parents_by_child` 映射。
3. 从目标成员开始向上 BFS。
4. 用 `visited` 去重，避免父母双边递归产生重复。
5. 最多展示 500 个祖先，超出时提示截断。

项目中的 `sql/core_queries.sql` 也保留了 Recursive CTE 版本，用于满足数据库实验要求。

### 19. 人物亲缘关系查询

```python
def kinship_path(genealogy_id, member_a_id, member_b_id, include_marriage=False, max_depth=20)
```

这个函数实现“输入两个人 ID，查询是否存在亲缘关系通路，并展示链路”。

核心思想是把族谱关系看作图：

- 父母 -> 子女
- 子女 -> 父母
- 如果勾选“包含婚姻关系”，再加入配偶 -> 配偶

然后用 BFS 搜索最短路径。

为什么用 BFS：

- BFS 找到的第一条路径就是最短路径。
- 可以设置 `max_depth`，避免在大图里无限扩展。
- 可以记录路径上的成员 ID，方便前端展示完整链路。

返回结果包括：

- `depth`：链路长度
- `labels`：关系方向，例如“父亲 -> 子女 -> 子女 -> 母亲”
- `members`：路径上的成员对象
- `visited_count`：搜索过程中访问了多少节点

### 20. 最近共同祖先

```python
def closest_common_ancestor(genealogy_id, member_a_id, member_b_id)
```

实现步骤：

1. 分别查询 A 和 B 的所有祖先及深度。
2. 取两个祖先集合的交集。
3. 按距离总和最小选择最近共同祖先。

这属于扩展功能，有助于解释两个成员是否来自同一支。

### 21. 统计分析函数

#### generation_lifespan_stats()

统计某个家族平均寿命最长的世代。

寿命计算方式：

```sql
COALESCE(death_year, 当前年份) - birth_year
```

如果没有卒年，就用当前年份估算年龄。

#### unmarried_males()

查询年龄大于指定阈值，且没有配偶的男性成员。

核心条件：

```sql
gender = 'M'
当前年份 - birth_year > age
NOT EXISTS 婚姻记录
```

#### early_birth_members()

找出出生年份早于本世代平均出生年份的成员。

先用 CTE 计算每一代平均出生年，再和成员表连接。

#### generation_profile()

统计每一代：

- 人数
- 男性人数
- 女性人数
- 最早出生年
- 最晚出生年

### 22. 分支导出

路由：

```python
@app.route("/genealogies/<int:genealogy_id>/export")
```

用户传入：

```text
root_id
```

系统调用 `descendants_flat(root_id)` 得到当前分支的所有后代，然后写入：

```text
exports/genealogy_<genealogy_id>_branch_<root_id>.csv
```

导出的字段包括：

- id
- name
- gender
- birth_year
- death_year
- generation
- depth

### 23. CLI 命令

`app.py` 注册了两个 Flask CLI 命令。

#### init-db

```bash
flask --app app init-db
```

调用：

```python
init_db()
```

读取 `schema.sql`，创建表、约束、索引、触发器和 FTS 表。

#### seed-demo

```bash
flask --app app seed-demo
```

调用：

```python
seed_demo()
```

写入一个小型演示库：

- 用户：`admin / admin123`
- 族谱：李氏示例族谱
- 成员：13 人
- 亲子关系：16 条
- 婚姻关系：4 条
- 世代：1 到 5 代

### 24. 数据库访问封装

`app.py` 中没有在各处直接创建连接，而是封装了几个函数：

```python
get_db()
query_all()
query_one()
query_value()
execute()
```

好处：

- 统一开启外键约束。
- 统一设置 WAL、busy_timeout 等 SQLite 参数。
- 统一事务提交。
- 路由代码更简洁。

`execute()` 使用：

```python
with db:
    db.execute(sql, params)
```

如果执行过程中出现约束错误，SQLite 会自动回滚本次事务。

### 25. app.py 答辩总结

可以这样概括 `app.py`：

> `app.py` 是系统主入口，基于 Flask 实现多用户族谱管理。它通过 Session 完成登录状态维护，通过数据库外键、触发器和应用层权限函数保证数据安全；查询中心把亲子关系和婚姻关系抽象成树或图，实现祖先查询、后代查询和亲缘链路查询；统计分析部分使用 SQL 聚合和 CTE 完成实验要求的数据查询。

## 二、scripts/generate_data.py 讲解

### 1. 文件整体职责

`scripts/generate_data.py` 用来生成满足实验要求的大规模模拟数据。

运行命令：

```bash
python scripts/generate_data.py --out data/generated --large-size 50000 --total-size 100000
```

生成结果是 5 个 CSV 文件：

```text
users.csv
genealogies.csv
members.csv
parent_child_relations.csv
marriages.csv
```

这些 CSV 后续通过 `scripts/import_sqlite.sql` 导入 SQLite 数据库。

### 2. 主要常量

```python
SURNAMES = ["赵", "钱", "孙", "李", "周", "吴", "郑", "王", "冯", "陈"]
MALE_NAMES = [...]
FEMALE_NAMES = [...]
ADMIN_PASSWORD = "admin123"
```

含义：

- `SURNAMES`：生成 10 个族谱，每个姓氏对应一个族谱。
- `MALE_NAMES`：男性名字池。
- `FEMALE_NAMES`：女性名字池。
- `ADMIN_PASSWORD`：生成的 `admin` 用户可直接用 `admin123` 登录。

### 3. main()

```python
def main() -> None:
```

脚本入口函数。

主要步骤：

1. 解析命令行参数。
2. 设置随机种子。
3. 创建输出目录。
4. 计算每个族谱的人数。
5. 写出用户 CSV。
6. 写出族谱 CSV。
7. 写出成员、亲子关系、婚姻关系 CSV。

命令行参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--out` | `data/generated` | CSV 输出目录 |
| `--large-size` | `50000` | 第一个大族谱成员数 |
| `--total-size` | `100000` | 全系统成员总数下限 |
| `--seed` | `20260514` | 随机种子，保证可复现 |

### 4. build_sizes()

```python
def build_sizes(large_size: int, total_size: int) -> list[int]:
```

这个函数负责给 10 个族谱分配成员数量。

规则：

1. 第一个族谱人数必须大于等于 50,000。
2. 全系统总人数必须大于等于 100,000。
3. 剩余 9 个族谱随机分配剩余人数。
4. 每个小族谱至少 60 人，保证能生成 30 代。

为什么最少是 60？

因为脚本每个族谱生成 30 代，每代至少 2 人。如果少于 60 人，就很难保证每一代都有成员。

核心逻辑：

```python
remaining = total_size - large_size
min_branch_size = 60
extra = remaining - min_branch_size * 9
```

然后使用随机切分方式把 `extra` 分给 9 个族谱。

最终返回：

```python
[large_size] + 9 个随机小族谱人数
```

例如一次生成结果可能是：

```text
[50000, 1789, 8348, 1874, 2547, 4867, 10202, 7312, 2703, 10358]
```

总和为 100000。

### 5. write_users()

```python
def write_users(out_dir: Path) -> None:
```

生成 `users.csv`。

字段：

```text
id, username, password_hash
```

写入规则：

- `admin` 用户写入有效密码 hash，对应密码是 `admin123`。
- `user2` 到 `user11` 保持占位密码 hash。

代码逻辑：

```python
writer.writerow([1, "admin", admin_password_hash])
for i in range(2, 12):
    writer.writerow([i, f"user{i}", "pbkdf2:sha256:placeholder"])
```

这样做的原因是：

- 导入大数据后，可以直接用 `admin / admin123` 登录。
- 其他模拟用户只用于满足族谱创建者分布，不作为主要登录账号。

### 6. make_password_hash()

```python
def make_password_hash(password: str) -> str:
```

这个函数负责生成 Flask 可识别的密码 hash。

优先使用 Werkzeug：

```python
werkzeug_generate_password_hash(password)
```

如果运行脚本的 Python 环境没有安装 Werkzeug，就使用标准库生成兼容格式：

```python
pbkdf2:sha256:600000$salt$hash
```

这样脚本既能在虚拟环境里运行，也能在只有 Python 标准库的情况下生成可登录的 `admin` 密码。

注意：虽然用户说“不要哈希”，但当前 Flask 登录代码使用 `check_password_hash()`。如果 CSV 里保存明文 `admin123`，反而无法登录。因此脚本保存的是 `admin123` 对应的有效 hash。

### 7. write_genealogies()

```python
def write_genealogies(out_dir: Path, sizes: list[int]) -> list[dict[str, int | str]]:
```

生成 `genealogies.csv`。

字段：

```text
id, name, surname, revision_time, creator_user_id
```

每个姓氏生成一个族谱：

```text
赵氏族谱
钱氏族谱
孙氏族谱
...
陈氏族谱
```

每个族谱还会带一个 `size`，供后续生成成员时使用。

创建者分配逻辑：

```python
creator_user_id = 1 + (i % 10)
```

所以 10 个族谱会分散给不同用户创建。系统中 `admin` 在 Dashboard 中被设置为可以查看全部族谱，便于验收。

### 8. write_members_and_relations()

```python
def write_members_and_relations(out_dir, genealogies, sizes) -> None:
```

这是生成大数据的核心函数，会同时生成：

- `members.csv`
- `parent_child_relations.csv`
- `marriages.csv`

原因是成员、亲子关系和婚姻关系之间有 ID 依赖，放在同一个函数里更容易维护。

### 9. 成员生成逻辑

对每个族谱，脚本固定生成 30 代：

```python
for generation in range(1, 31):
```

每一代的人数由当前剩余人数平均到剩余世代：

```python
generation_size = max(2, remaining // generations_left)
```

这样可以保证：

- 每一代至少 2 人。
- 最后一代吃掉剩余人数。
- 每个族谱最终总人数准确等于分配人数。

成员字段包括：

```text
id
genealogy_id
name
gender
birth_year
death_year
generation
biography
```

### 10. 姓名、性别和年份生成

性别生成：

```python
gender = "M" if index % 2 == 0 else "F"
```

也就是同一代中男女交替生成。

姓名生成：

```python
name = f"{surname}{name_pool[index % len(name_pool)]}{generation}"
```

例如：

```text
李明1
李芳1
李强2
李娜2
```

出生年生成：

```python
birth_year = 1850 + generation * 24 + random.randint(-3, 3)
```

含义：

- 每代大约间隔 24 年。
- 加入 -3 到 3 年随机扰动，让数据更自然。

卒年生成：

```python
death_year = birth_year + random.randint(62, 88) if birth_year < 1945 else ""
```

较早出生的人生成卒年，较晚出生的人可能仍在世，卒年留空。

### 11. 亲子关系生成

从第 2 代开始，每个成员都会分配上一代的父亲和母亲：

```python
if generation > 1:
    parents = generation_members[generation - 2]
    fathers = parents[0::2] or parents
    mothers = parents[1::2] or parents
```

上一代偶数下标作为父亲候选，奇数下标作为母亲候选。

然后给当前代每个孩子写入两条关系：

```python
relations.writerow([father_id, child_id, "father"])
relations.writerow([mother_id, child_id, "mother"])
```

这保证：

- 第 2 到第 30 代成员都有父母关系。
- 每个族谱内成员都能和其他成员形成亲缘连接。
- 族谱存在连续 30 代传承。

第 1 代没有父母，但第 1 代成员会成为第 2 代的父母，因此也与其他成员存在亲缘关系。

### 12. 婚姻关系生成

每一代按相邻成员两两配对：

```python
for left, right in zip(current_generation[0::2], current_generation[1::2]):
    marriages.writerow([...])
```

即：

```text
第 1 个和第 2 个结婚
第 3 个和第 4 个结婚
...
```

婚姻字段包括：

```text
id
member1_id
member2_id
married_year
ended_year
status
```

默认状态：

```text
active
```

结婚年份根据世代估算：

```python
1870 + generation * 24
```

### 13. 输出文件说明

运行脚本后会生成：

#### users.csv

用户数据。

```text
admin 可以登录，密码 admin123
其他用户是占位账号
```

#### genealogies.csv

10 个族谱数据。

每个族谱包含：

- 谱名
- 姓氏
- 修谱时间
- 创建用户

#### members.csv

成员主表数据。

默认总数为 100000 条成员数据。

#### parent_child_relations.csv

亲子关系数据。

每个第 2 代以后成员都有父亲和母亲两条关系。

#### marriages.csv

婚姻关系数据。

用于配偶查询和可选的亲缘链路扩展。

### 14. 为什么生成 CSV 而不是直接写数据库

生成 CSV 有几个好处：

1. 满足实验要求中的批量导入导出。
2. 可以用 SQLite、MySQL、PostgreSQL 不同方式导入。
3. 数据生成和数据库导入解耦，方便验收展示。
4. 大文件可以单独检查，例如用 `wc -l` 验证成员数量。

### 15. 和导入脚本的关系

`generate_data.py` 只负责生成 CSV。

真正导入数据库的是：

```text
scripts/import_sqlite.sql
```

典型流程：

```bash
python scripts/generate_data.py --out data/generated --large-size 50000 --total-size 100000
flask --app app init-db
sqlite3 genealogy.db < scripts/import_sqlite.sql
```

导入顺序必须是：

1. users
2. genealogies
3. members
4. parent_child_relations
5. marriages

因为后面的表依赖前面的外键。

### 16. generate_data.py 答辩总结

可以这样概括：

> `generate_data.py` 通过 Python 随机生成 10 个族谱的 CSV 数据。第一个族谱默认 50,000 人，剩余 9 个族谱随机分配剩余人数，全系统默认 100,000 名成员。每个族谱固定生成 30 代，从第 2 代开始每个成员都有父母关系，因此保证族谱内部存在连续亲缘传承。脚本同时生成婚姻关系和可登录的 admin 用户，方便导入后直接验收。
