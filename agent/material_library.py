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
    root = Path(root).resolve()
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
            records = [{'path': i.filename, 'experiment': PurePosixPath(i.filename).parent.name or '未分组实验', 'experiment_category': PurePosixPath(i.filename).parent.parent.name or '未分类'}
                       for i in entries if not i.is_dir() and not any(p.startswith(('.', '__MACOSX')) for p in PurePosixPath(i.filename).parts)
                       and PurePosixPath(i.filename).suffix.lower() in ('.jpg', '.jpeg', '.png', '.docx')]
        if not records: raise ValueError('资料包中没有实验图片或 Word 样例')
        items = normalize_groups(read_json(root/'library.json', []))
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
                category = str(record.get('experiment_category') or read_json(Path(__file__).parent/'static/experiment-categories.json',{}).get(experiment) or '未分类')[:100]
                old_fingerprint = hashlib.sha256(experiment.encode()+b'\0'+content).hexdigest()
                fingerprint = hashlib.sha256(category.encode()+b'\0'+experiment.encode()+b'\0'+content).hexdigest()
                if fingerprint in seen: continue
                old = next((i for i in items if i.get('pack_fingerprint')==old_fingerprint and i.get('experiment_category') in (category,'未分类')), None)
                if old:
                    old.update(experiment_category=category,group_id=group_key(category,experiment),pack_fingerprint=fingerprint)
                    seen.add(fingerprint)
                    continue
                seen.add(fingerprint)
                identifier = uuid.uuid4().hex
                dest = root/'library'/(identifier+suffix)
                (Path(temp)/dest.name).write_bytes(content)
                added.append({'id': identifier, 'name': PurePosixPath(path).name, 'path': str(dest),
                              'category': 'examples' if suffix == '.docx' else 'materials',
                              'experiment': experiment, 'experiment_category':category,'group_id':group_key(category,experiment), 'pack_fingerprint': fingerprint})
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


def group_key(category, name):
    return 'experiment-'+hashlib.sha256((category+'\0'+name).encode()).hexdigest()[:24]


def normalize_groups(items):
    """Upgrade earlier flat libraries without moving files used by old reports."""
    catalog = read_json(Path(__file__).parent/'static/experiment-categories.json', {})
    for item in items:
        if item.get('group_id') or (item['category'] != 'materials' and not item.get('experiment')):
            continue
        name = item.get('experiment') or '历史上传资料'
        category = item.get('experiment_category') or (catalog.get(name) if item.get('pack_fingerprint') else None) or '未分类'
        item.update(experiment=name, experiment_category=category, group_id=group_key(category,name))
    return items


def list_groups(items):
    groups = {}
    for item in items:
        identifier = item.get('group_id')
        if not identifier: continue
        group = groups.setdefault(identifier, {'id': identifier, 'name': item['experiment'],
                                  'category': item['experiment_category'], 'items': []})
        group['items'].append(item['id'])
    return list(groups.values())


def upload_group(files, root, name='', category='', identifier=''):
    root = Path(root).resolve()
    items = normalize_groups(read_json(root/'library.json', []))
    if identifier:
        group = next((g for g in list_groups(items) if g['id']==identifier), None)
        if not group: raise ValueError('资料组不存在，请重新选择')
        name, category = group['name'], group['category']
    else:
        name, category = name.strip(), category.strip()
        if not name or not category: raise ValueError('请填写实验大类和实验名称')
        if len(name)>200 or len(category)>100: raise ValueError('实验名称或大类过长')
        if any(g['name']==name and g['category']==category for g in list_groups(items)):
            raise ValueError('该大类下已有同名实验，请选择追加到已有资料组')
        identifier = uuid.uuid4().hex
    if not files: raise ValueError('请至少选择一张实验资料图片或 Word 样例')
    added = []
    with tempfile.TemporaryDirectory(dir=root) as temp:
        for upload in files:
            filename = Path(upload.filename.replace('\\','/')).name
            suffix = Path(filename).suffix.lower()
            if suffix not in ('.png','.jpg','.jpeg','.docx'): raise ValueError('资料组只支持 PNG/JPG 图片和 DOCX 样例')
            dest = root/'library'/(uuid.uuid4().hex+suffix)
            staged = Path(temp)/dest.name
            upload.save(staged)
            try:
                if suffix=='.docx':
                    with zipfile.ZipFile(staged) as doc:
                        if 'word/document.xml' not in doc.namelist(): raise ValueError()
                else:
                    with Image.open(staged) as img: img.verify()
            except Exception as exc: raise ValueError('文件损坏或格式错误：'+filename) from exc
            added.append({'id':uuid.uuid4().hex,'name':filename,'path':str(dest),
                          'category':'examples' if suffix=='.docx' else 'materials',
                          'group_id':identifier,'experiment':name,'experiment_category':category})
        (root/'library').mkdir(exist_ok=True)
        moved=[]
        try:
            for item in added:
                dest=Path(item['path']);shutil.move(str(Path(temp)/dest.name),dest);moved.append(dest)
            write_json(root/'library.json',items+added)
        except Exception:
            for dest in moved:dest.unlink(missing_ok=True)
            raise
    return identifier, added
