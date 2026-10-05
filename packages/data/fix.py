with open('后端/存储/数据库.py', 'r') as f:
    content = f.read()

old = """            SELECT t.tablename, n.nspname as schema_name,
                   pg_size_pretty(pg_total_relation_size(c.oid)) as size
            FROM pg_tables t
            JOIN pg_class c ON c.relname = t.tablename AND c.relnamespace = n.oid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
              AND n.nspname NOT LIKE E'pg\\\\_toast%'
              AND n.nspname NOT LIKE E'pg\\\\_temp%'
            ORDER BY n.nspname, t.tablename"""

new = """            SELECT t.tablename, n.nspname as schema_name,
                   pg_size_pretty(pg_total_relation_size(c.oid)) as size
            FROM pg_tables t
            JOIN pg_namespace n ON n.nspname = t.schemaname
            JOIN pg_class c ON c.relname = t.tablename AND c.relnamespace = n.oid
            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
              AND n.nspname NOT LIKE 'pg\\\\_toast' ESCAPE '\\\\'
              AND n.nspname NOT LIKE 'pg\\\\_temp' ESCAPE '\\\\'
            ORDER BY n.nspname, t.tablename"""

content = content.replace(old, new)

# Also fix list_schemas
old2 = "AND nspname NOT SIMILAR TO 'pg\\\\_toast|pg\\\\_temp'"
new2 = "AND nspname NOT LIKE 'pg\\_toast' ESCAPE '\\\\'\n              AND nspname NOT LIKE 'pg\\_temp' ESCAPE '\\\\'"
content = content.replace(old2, new2)

with open('后端/存储/数据库.py', 'w') as f:
    f.write(content)
print("Fixed joins and LIKE")
