#!/usr/bin/env python3
"""重置数据库脚本 - 删除旧数据库并重新创建"""

import os
import sys
from pathlib import Path

# 删除所有可能的数据库文件
db_files = [
    'app.db',
    'instance/app.db',
    'database.db'
]

for db_file in db_files:
    path = Path(db_file)
    if path.exists():
        try:
            # 确保文件没有被占用
            path.unlink()
            print(f"[OK] 已删除: {db_file}")
        except Exception as e:
            print(f"[ERROR] 删除失败 {db_file}: {e}")

# 删除迁移文件夹
migrations_dir = Path('migrations')
if migrations_dir.exists():
    import shutil
    try:
        shutil.rmtree(migrations_dir)
        print(f"[OK] 已删除: migrations/")
    except Exception as e:
        print(f"[ERROR] 删除失败 migrations/: {e}")

print("\n数据库重置完成，现在初始化...")
print("=" * 50)

# 导入并启动应用
from app import app, db

with app.app_context():
    # 创建所有表
    db.create_all()
    print("[OK] 数据库表已创建")
    
    # 初始化管理员
    from app import init_admin
    init_admin()
    print("[OK] 管理员账号已初始化")
    
    print("\n" + "=" * 50)
    print("数据库初始化完成！")
    print("请手动启动应用: python app.py")
