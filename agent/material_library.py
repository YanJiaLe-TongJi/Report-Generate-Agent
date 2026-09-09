"""Portable experiment packs. Never extract archive paths into the filesystem."""
import hashlib
import io
import json
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from PIL import Image
from storage import read_json, write_json

LIMIT = 200 * 1024 * 1024

def import_pack(stream, root):
    root = Path(root)
    try:
        archive = zipfile.ZipFile(stream)
    except zipfile.BadZipFile as exc:
        raise ValueError('请选择有效的 ZIP 资料包') from exc
    with archive:
        entries = archive.infolist()
        if len(entries) > 3000 or sum(i.file_size for i in entries) > LIMIT:
            raise ValueError('资料包解压后不能超过 200MB 或 3000 个文件')
        names = set()
        for info in entries:
            p = PurePosixPath(info.filename)
            if p.is_absolute() or '..' in p.parts or '\\' in info.filename or info.filename in names:
                raise ValueError('资料包含不安全或重复的路径')
            names.add(info.filename)
        manifest = {}
        if 'manifest.json' in names:
            try:
                manifest = json.loads(archive.read('manifest.json'))
                if manifest.get('format') != 'physics-report-library-v1':
                    raise ValueError()
                records = manifest['files']
                if not isinstance(records, list): raise ValueError()
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise ValueError('资料包清单格式不正确') from exc
        else:
            records = [{'path': i.filename, 'experiment': PurePosixPath(i.filename).parent.name or '未分组实验'}
                       for i in entries if not i.is_dir() and not any(p.startswith(('.', '__MACOSX')) for p in PurePosixPath(i.filename).parts)
                       and PurePosixPath(i.filename).suffix.lower() in ('.jpg', '.jpeg', '.png', '.docx')]
        if not records: raise ValueError('资料包中没有实验图片或 Word 样例')
        items = read_json(root/'library.json', [])
        seen = {i.get('pack_fingerprint') for i in items}
        added = []
        with tempfile.TemporaryDirectory(dir=root) as temp:
            for record in records:
                if not isinstance(record, dict): raise ValueError('资料包清单格式不正确')
                path = record.get('path', '')
                if not isinstance(path, str) or path not in names: raise ValueError('资料包缺少清单中的文件')
                suffix = PurePosixPath(path).suffix.lower()
                if suffix not in ('.jpg', '.jpeg', '.png', '.docx'): raise ValueError('资料包只支持 PNG/JPG 图片和 DOCX 样例')
                content = archive.read(path)
                if suffix != '.docx':
                    try:
                        with Image.open(io.BytesIO(content)) as img: img.verify()
                    except Exception as exc: raise ValueError('资料包含损坏的图片：'+path) from exc
                elif not zipfile.is_zipfile(io.BytesIO(content)):
                    raise ValueError('资料包含无效的 Word 样例')
                experiment = str(record.get('experiment') or '未分组实验')[:200]
                fingerprint = hashlib.sha256(experiment.encode()+b'\0'+content).hexdigest()
                if fingerprint in seen: continue
                seen.add(fingerprint)
                identifier = uuid.uuid4().hex
                dest = root/'library'/(identifier+suffix)
                (Path(temp)/dest.name).write_bytes(content)
                added.append({'id': identifier, 'name': PurePosixPath(path).name, 'path': str(dest),
                              'category': 'examples' if suffix == '.docx' else 'materials',
                              'experiment': experiment, 'pack_fingerprint': fingerprint})
            folder = root/'library'; folder.mkdir(exist_ok=True)
            moved = []
            try:
                for item in added:
                    dest = Path(item['path']); shutil.move(str(Path(temp)/dest.name), dest); moved.append(dest)
                write_json(root/'library.json', items+added)
            except Exception:
                for dest in moved: dest.unlink(missing_ok=True)
                raise
        return added
