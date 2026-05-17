# Genealogy-System

数据库实验用族谱管理系统，基于 Flask + SQLite 实现。系统覆盖注册登录、族谱权限、成员 CRUD、协作者邀请、树形预览、祖先递归查询、亲缘链路查询、CSV 导入导出、核心 SQL、索引与范式说明。

## VS Code 环境配置

建议环境：

- Python 3.10+
- VS Code Python 插件
- 可选：SQLite Viewer / SQLite 扩展，方便查看 `genealogy.db`

创建虚拟环境并安装依赖：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

初始化数据库并写入演示数据：

```bash
flask --app app init-db
flask --app app seed-demo
```

启动系统：

```bash
flask --app app run --debug
```

浏览器打开：

```text
http://127.0.0.1:5000
```

演示账号：

```text
admin / admin123
```

## 项目结构

```text
app.py                    Flask 应用
schema.sql                建表、约束、触发器、索引
sql/core_queries.sql      实验要求的核心 SQL
scripts/generate_data.py  十万级模拟数据生成脚本
docs/database_design.md   ER、关系模式、范式、索引、性能对比说明
docs/import_export.md     CSV 生成、LOAD DATA/COPY、导出说明
templates/                页面模板
static/style.css          页面样式
```

## 大数据模拟

生成 10 个族谱、总成员 100000 人、一个族谱 50000 人以上：

```bash
python scripts/generate_data.py --out data/generated --large-size 50000 --total-size 100000
```

导入方式见 [docs/import_export.md](docs/import_export.md)。

## 实验材料

- 数据库设计：[docs/database_design.md](docs/database_design.md)
- 项目亮点整理：[docs/project_highlights.md](docs/project_highlights.md)
- 查询功能讲解：[docs/query_guide.md](docs/query_guide.md)
- 核心 SQL：[sql/core_queries.sql](sql/core_queries.sql)
- 导入导出：[docs/import_export.md](docs/import_export.md)
