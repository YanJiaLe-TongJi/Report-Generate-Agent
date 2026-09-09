"""Export only active experiment images/examples from the Web SQLite database."""
import argparse
import json
import sqlite3
import zipfile
from pathlib import Path


def export(database, uploads, output):
    uploads = Path(uploads).resolve()
    records = []
    with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute('SELECT experiment_name, material_paths, example_path, default_cover FROM system_materials WHERE is_active=1').fetchall()
    for row in rows:
        paths = json.loads(row['material_paths'] or '[]')
        if row['example_path']: paths.append(row['example_path'])
        for raw in paths:
            parts = Path(raw.replace('\\', '/')).parts
            if 'uploads' in parts:
                source = uploads.joinpath(*parts[parts.index('uploads')+1:]).resolve()
            else:
                source = (uploads/raw).resolve()
            if uploads not in source.parents or not source.is_file():
                raise ValueError('资料文件不存在或不在 uploads 内：'+str(raw))
            records.append((source, row['experiment_name'], (json.loads(row['default_cover'] or '{}') or {}).get('category') or '未分类'))
    if not records: raise ValueError('没有可导出的有效资料')
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        manifest = []
        for index, (source, experiment, category) in enumerate(records):
            name = f'files/{index:04d}/{source.name}'
            archive.write(source, name)
            manifest.append({'path': name, 'experiment': experiment, 'experiment_category': category})
        archive.writestr('manifest.json', json.dumps({'format': 'physics-report-library-v1', 'files': manifest}, ensure_ascii=False))
    print(f'导出 {len(records)} 个文件：{output}')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--uploads', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    export(args.database, args.uploads, args.output)
